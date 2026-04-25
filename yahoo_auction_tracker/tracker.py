from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import requests

from .config import DEFAULT_SETTINGS, WORLD_TIME_API_URL
from .database import get_or_create_search, init_db, save_items
from .scraper import RateLimitError, ScraperError, build_session, search_active, search_closed

logger = logging.getLogger(__name__)

JST = ZoneInfo("Asia/Tokyo")


def get_japan_time() -> datetime:
    """
    Fetch the current Japan Standard Time from worldtimeapi.org.
    Falls back to system time converted to JST if the request fails.
    """
    try:
        resp = requests.get(WORLD_TIME_API_URL, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        # Response contains 'datetime' like '2024-05-01T14:30:00.123456+09:00'
        dt_str = data.get("datetime", "")
        dt = datetime.fromisoformat(dt_str)
        logger.debug("Japan time from worldtimeapi.org: %s", dt)
        return dt
    except Exception as exc:
        logger.warning("Could not fetch Japan time from web (%s), using system clock.", exc)
        return datetime.now(tz=JST)


def get_japan_date_str() -> str:
    """Return today's date in Japan as YYYY-MM-DD string."""
    return get_japan_time().strftime("%Y-%m-%d")


class AuctionTracker:
    def __init__(
        self,
        db_path: str = DEFAULT_SETTINGS["db_path"],
        delay: float = DEFAULT_SETTINGS["request_delay_seconds"],
        max_pages: int = DEFAULT_SETTINGS["max_pages"],
    ) -> None:
        self.db_path = db_path
        self.delay = delay
        self.max_pages = max_pages
        self._session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = build_session()
        return self._session

    def run_daily_update(
        self,
        keyword: str,
        category_id: Optional[str] = None,
        *,
        include_closed: bool = True,
    ) -> dict:
        snapshot_date = get_japan_date_str()
        logger.info(
            "Starting daily update — keyword=%r category=%s date=%s",
            keyword, category_id or "any", snapshot_date,
        )
        result: dict = {
            "keyword": keyword,
            "category_id": category_id,
            "snapshot_date": snapshot_date,
            "active_count": 0,
            "closed_count": 0,
            "new_items": 0,
            "updated_items": 0,
            "errors": [],
        }
        conn = init_db(self.db_path)
        try:
            search_id = get_or_create_search(conn, keyword, category_id)
            session = self._get_session()

            # Active auctions
            try:
                active_items = search_active(
                    session, keyword, category_id,
                    max_pages=self.max_pages, delay=self.delay,
                )
                ins, upd = save_items(conn, search_id, active_items, snapshot_date)
                result["active_count"] = len(active_items)
                result["new_items"] += ins
                result["updated_items"] += upd
                logger.info("Active: %d items (%d new, %d updated)", len(active_items), ins, upd)
            except (ScraperError, RateLimitError) as exc:
                msg = f"Active search error: {exc}"
                logger.error(msg)
                result["errors"].append(msg)

            # Closed auctions
            if include_closed:
                try:
                    closed_items = search_closed(
                        session, keyword, category_id,
                        max_pages=self.max_pages, delay=self.delay,
                    )
                    ins, upd = save_items(conn, search_id, closed_items, snapshot_date)
                    result["closed_count"] = len(closed_items)
                    result["new_items"] += ins
                    result["updated_items"] += upd
                    logger.info("Closed: %d items (%d new, %d updated)", len(closed_items), ins, upd)
                except (ScraperError, RateLimitError) as exc:
                    msg = f"Closed search error: {exc}"
                    logger.error(msg)
                    result["errors"].append(msg)
        finally:
            conn.close()

        return result

    def get_summary(
        self,
        keyword: str,
        category_id: Optional[str] = None,
        days: int = 30,
    ) -> dict:
        from .database import get_price_summary
        conn = init_db(self.db_path)
        try:
            return get_price_summary(conn, keyword, category_id, days=days)
        finally:
            conn.close()

    def schedule_daily(
        self,
        keyword: str,
        category_id: Optional[str] = None,
        *,
        run_time: str = "09:00",
        include_closed: bool = True,
        run_now: bool = False,
    ) -> None:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger

        if run_now:
            logger.info("Running immediate update before scheduling...")
            self.run_daily_update(keyword, category_id, include_closed=include_closed)

        hour, minute = run_time.split(":")
        scheduler = BlockingScheduler(timezone=JST)
        scheduler.add_job(
            self.run_daily_update,
            CronTrigger(hour=int(hour), minute=int(minute), timezone=JST),
            args=[keyword, category_id],
            kwargs={"include_closed": include_closed},
            id="daily_update",
            name=f"Daily update: {keyword}",
            max_instances=1,
            misfire_grace_time=3600,
        )
        logger.info(
            "Scheduled daily update for %r at %s JST. Press Ctrl+C to stop.", keyword, run_time
        )
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Scheduler stopped.")
            scheduler.shutdown(wait=False)

    def close(self) -> None:
        if self._session:
            self._session.close()
            self._session = None
