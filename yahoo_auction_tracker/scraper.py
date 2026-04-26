from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import BASE_SEARCH_URL, CLOSED_SEARCH_URL, DEFAULT_SETTINGS

logger = logging.getLogger(__name__)

_CAPTCHA_MARKERS = ("確認が必要", "お使いのブラウザではご利用になれません", "アクセスが制限されています")

# Matches /auction/{id} (relative) and page.auctions.yahoo.co.jp/jp/auction/{id}
_AUCTION_HREF_RE = re.compile(r"/auction/([A-Za-z0-9]+)")


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
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://auctions.yahoo.co.jp/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Upgrade-Insecure-Requests": "1",
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


def warm_session(session: requests.Session, *, category_id: Optional[str] = None) -> None:
    """Visit Yahoo Japan auction pages to obtain session cookies.

    Yahoo Japan requires a valid session cookie before accepting search
    requests. When a category is specified we also visit the category search
    page so the session is primed for category-filtered results.
    """
    try:
        session.get("https://auctions.yahoo.co.jp/", timeout=15)
        if category_id:
            session.get(
                BASE_SEARCH_URL,
                params={"auccat": category_id},
                timeout=15,
            )
        logger.debug("Session warmed up (category_id=%s).", category_id)
    except Exception as exc:
        logger.debug("Session warm-up failed (continuing anyway): %s", exc)


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


def _extract_item_id(url: str) -> Optional[str]:
    m = _AUCTION_HREF_RE.search(url)
    return m.group(1) if m else None


def _parse_yen(text: str) -> Optional[int]:
    cleaned = re.sub(r"[¥￥,円\s\xa5]", "", text)
    return int(cleaned) if cleaned.isdigit() else None


def _extract_prices(text: str) -> tuple[Optional[int], Optional[int]]:
    """Return (current_price, buynow_price) parsed from container text."""
    hits = re.findall(r"[¥￥]([\d,]+)", text)
    prices = []
    for h in hits:
        raw = h.replace(",", "")
        if raw.isdigit():
            prices.append(int(raw))
    return (prices[0] if prices else None, prices[1] if len(prices) > 1 else None)


def _parse_condition(text: str) -> Optional[str]:
    if "新品" in text:
        return "new"
    if "中古" in text:
        return "used"
    return None


def _item_container(anchor: BeautifulSoup) -> BeautifulSoup:
    """Walk up the DOM from an auction anchor to find the per-item container.

    Stops at the first ancestor that contains only ONE auction item link,
    which is the per-item card/row regardless of class names.
    """
    node = anchor
    for _ in range(8):
        parent = node.find_parent(["li", "div", "article", "section"])
        if parent is None:
            break
        # Count how many distinct auction IDs live in this parent
        ids = {_extract_item_id(a["href"]) for a in parent.find_all("a", href=_AUCTION_HREF_RE) if a.get("href")}
        ids.discard(None)
        if len(ids) > 1:
            # Too many items — the previous level was the right container
            break
        node = parent
    return node


def parse_items(soup: BeautifulSoup, *, is_closed: bool = False) -> list[AuctionItem]:
    """Extract auction items from a search result page.

    Uses auction item URLs as the discovery anchor instead of CSS class
    names so the parser stays robust across Yahoo Japan HTML changes.
    """
    results: list[AuctionItem] = []
    seen: set[str] = set()

    anchors = soup.find_all("a", href=_AUCTION_HREF_RE)
    logger.debug("Found %d auction anchor tags on page.", len(anchors))

    for anchor in anchors:
        try:
            href = anchor.get("href", "")
            item_id = _extract_item_id(href)
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)

            item_url = href if href.startswith("http") else urljoin("https://auctions.yahoo.co.jp", href)

            container = _item_container(anchor)
            text = container.get_text(" ", strip=True)

            # Title: prefer anchor text; fall back to nearest img alt
            title = anchor.get_text(strip=True)
            if not title:
                img = anchor.find("img")
                if img:
                    title = img.get("alt", "")
            if not title or len(title) < 2:
                continue

            current_price, buynow_price = _extract_prices(text)

            bid_m = re.search(r"(\d+)\s*入札", text)
            bid_count = int(bid_m.group(1)) if bid_m else 0

            time_m = re.search(r"(残り[^\s　]{1,15})", text)
            time_remaining = time_m.group(1) if time_m else None

            condition = _parse_condition(text)

            # Seller: look for a link near 出品者
            seller_id: Optional[str] = None
            seller_m = re.search(r"出品者[：:\s]*([A-Za-z0-9_\-]+)", text)
            if seller_m:
                seller_id = seller_m.group(1)

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
            logger.debug("Skipping item", exc_info=True)
            continue

    if not results and anchors:
        logger.warning(
            "Found %d auction links but parsed 0 items — "
            "check page structure with --verbose.",
            len(anchors),
        )

    return results


def parse_total_count(soup: BeautifulSoup) -> int:
    m = re.search(r"([\d,]+)\s*件", soup.get_text())
    return int(m.group(1).replace(",", "")) if m else 0


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

    # Warm up session cookies; prime category context when filtering by category
    warm_session(session, category_id=category_id)

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

        logger.debug("Fetching page %d | params: %s", page + 1, params)

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
