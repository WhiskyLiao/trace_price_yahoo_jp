from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import BASE_SEARCH_URL, CLOSED_SEARCH_URL, DEFAULT_SETTINGS

logger = logging.getLogger(__name__)

_CAPTCHA_MARKERS = ("確認が必要", "お使いのブラウザではご利用になれません", "アクセスが制限されています")


class ScraperError(Exception):
    pass


class RateLimitError(ScraperError):
    pass


@dataclass
class AuctionItem:
    item_id: str
    title: str
    item_url: str
    current_price: Optional[int]
    buynow_price: Optional[int]
    bid_count: int
    time_remaining: Optional[str]
    condition: Optional[str]
    seller_id: Optional[str]
    is_closed: bool = False


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": DEFAULT_SETTINGS["user_agent"],
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _check_blocked(resp: requests.Response) -> None:
    if resp.status_code == 429:
        raise RateLimitError(f"Rate limited (HTTP 429): {resp.url}")
    if resp.status_code == 403:
        raise RateLimitError(f"Access forbidden (HTTP 403): {resp.url}")
    if resp.status_code != 200:
        raise ScraperError(f"Unexpected HTTP {resp.status_code}: {resp.url}")
    for marker in _CAPTCHA_MARKERS:
        if marker in resp.text:
            raise RateLimitError(f"CAPTCHA/block page detected at {resp.url}")


def fetch_page(
    session: requests.Session,
    url: str,
    params: dict,
    *,
    timeout: int = 15,
) -> BeautifulSoup:
    try:
        resp = session.get(url, params=params, timeout=timeout)
    except requests.Timeout as exc:
        raise ScraperError(f"Timeout fetching {url}") from exc
    except requests.ConnectionError as exc:
        raise ScraperError(f"Connection error fetching {url}") from exc
    _check_blocked(resp)
    return BeautifulSoup(resp.text, "html.parser")


def _parse_yen(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    cleaned = re.sub(r"[¥,円\s\xa5]", "", text.strip())
    return int(cleaned) if cleaned.isdigit() else None


def _extract_item_id(url: str) -> Optional[str]:
    m = re.search(r"/auction/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None


def _parse_condition(text: str) -> Optional[str]:
    if "新品" in text:
        return "new"
    if "中古" in text:
        return "used"
    return None


def parse_items(soup: BeautifulSoup, *, is_closed: bool = False) -> list[AuctionItem]:
    results: list[AuctionItem] = []

    containers = soup.select("li.Product") or soup.select("div.Product")
    if not containers:
        # Fallback: try generic search result structure
        containers = soup.select("[class*='Product']")

    zero_price_count = 0

    for container in containers:
        try:
            # Title and URL
            title_tag = (
                container.select_one("h3.Product__title a")
                or container.select_one(".Product__title a")
                or container.select_one("a.Product__imageLink")
            )
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            raw_url = title_tag.get("href", "")
            if not raw_url:
                continue
            item_url = raw_url if raw_url.startswith("http") else urljoin("https://auctions.yahoo.co.jp", raw_url)
            item_id = _extract_item_id(item_url)
            if not item_id:
                continue

            # Current price
            price_tag = (
                container.select_one("span.Product__price")
                or container.select_one(".Product__priceValue")
                or container.select_one("dd.Product__currentPrice")
                or container.select_one("[class*='price']")
            )
            current_price = _parse_yen(price_tag.get_text() if price_tag else None)
            if current_price is None:
                zero_price_count += 1

            # Buy-now price
            buynow_tag = (
                container.select_one(".Product__bidNowPrice")
                or container.select_one("dd.Product__bidNow")
                or container.select_one("[class*='bidNow']")
            )
            buynow_price = _parse_yen(buynow_tag.get_text() if buynow_tag else None)

            # Bid count
            bid_tag = (
                container.select_one("dd.Product__bid a")
                or container.select_one(".Product__bidNum")
                or container.select_one("[class*='bid']")
            )
            bid_text = bid_tag.get_text(strip=True) if bid_tag else "0"
            bid_match = re.search(r"(\d+)", bid_text)
            bid_count = int(bid_match.group(1)) if bid_match else 0

            # Time remaining
            time_tag = (
                container.select_one("dd.Product__time")
                or container.select_one(".Product__timeRemaining")
                or container.select_one("[class*='time']")
            )
            time_remaining = time_tag.get_text(strip=True) if time_tag else None

            # Condition
            cond_tag = container.select_one(".Product__condition") or container.select_one("[class*='condition']")
            condition = _parse_condition(cond_tag.get_text() if cond_tag else "")

            # Seller
            seller_tag = container.select_one("a.Product__seller") or container.select_one("[class*='seller'] a")
            seller_id = seller_tag.get_text(strip=True) if seller_tag else None

            results.append(
                AuctionItem(
                    item_id=item_id,
                    title=title,
                    item_url=item_url,
                    current_price=current_price,
                    buynow_price=buynow_price,
                    bid_count=bid_count,
                    time_remaining=time_remaining,
                    condition=condition,
                    seller_id=seller_id,
                    is_closed=is_closed,
                )
            )
        except Exception:
            logger.debug("Skipping malformed item", exc_info=True)
            continue

    if containers and zero_price_count > len(containers) * 0.5:
        logger.warning(
            "Over 50%% of items on page had no price parsed — "
            "Yahoo may have changed their HTML structure."
        )

    return results


def parse_total_count(soup: BeautifulSoup) -> int:
    for selector in (".SearchMode__count", ".Result__count", "[class*='count']"):
        tag = soup.select_one(selector)
        if tag:
            m = re.search(r"([\d,]+)", tag.get_text())
            if m:
                return int(m.group(1).replace(",", ""))
    return 0


def _paginate(
    session: requests.Session,
    base_url: str,
    keyword: str,
    category_id: Optional[str],
    *,
    max_pages: int,
    delay: float,
    is_closed: bool,
) -> list[AuctionItem]:
    items_per_page = DEFAULT_SETTINGS["items_per_page"]
    all_items: list[AuctionItem] = []
    seen_ids: set[str] = set()

    for page in range(max_pages):
        offset = page * items_per_page + 1
        params: dict = {
            "p": keyword,
            "n": items_per_page,
            "b": offset,
            "s1": "new",
            "o1": "d",
        }
        if category_id:
            params["auccat"] = category_id

        try:
            soup = fetch_page(session, base_url, params)
        except RateLimitError:
            logger.warning("Rate limit hit on page %d, stopping early.", page + 1)
            break
        except ScraperError as exc:
            logger.error("Scrape error on page %d: %s", page + 1, exc)
            break

        page_items = parse_items(soup, is_closed=is_closed)
        new_items = [i for i in page_items if i.item_id not in seen_ids]
        seen_ids.update(i.item_id for i in new_items)
        all_items.extend(new_items)

        logger.info("Page %d: fetched %d items (total so far: %d)", page + 1, len(new_items), len(all_items))

        if len(page_items) < items_per_page:
            break

        if page < max_pages - 1:
            time.sleep(delay)

    return all_items


def search_active(
    session: requests.Session,
    keyword: str,
    category_id: Optional[str] = None,
    *,
    max_pages: int = DEFAULT_SETTINGS["max_pages"],
    delay: float = DEFAULT_SETTINGS["request_delay_seconds"],
) -> list[AuctionItem]:
    return _paginate(
        session, BASE_SEARCH_URL, keyword, category_id,
        max_pages=max_pages, delay=delay, is_closed=False,
    )


def search_closed(
    session: requests.Session,
    keyword: str,
    category_id: Optional[str] = None,
    *,
    max_pages: int = DEFAULT_SETTINGS["max_pages"],
    delay: float = DEFAULT_SETTINGS["request_delay_seconds"],
) -> list[AuctionItem]:
    return _paginate(
        session, CLOSED_SEARCH_URL, keyword, category_id,
        max_pages=max_pages, delay=delay, is_closed=True,
    )
