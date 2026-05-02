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

# Listing-page badge / promo labels that share the same /auction/{id} href as
# the real title link. Picking one of these as the item title produces rows
# like "New!!" with no other info, so we skip them when scoring candidates.
_PROMO_LABEL_RE = re.compile(
    r"^(New!{0,3}|NEW!{0,3}|急上昇|注目|まもなく終了|残り[^\s　]+|[\d,]+円|送料無料|"
    r"即決|入札\d+|落札\d+|現在|最高額|残り\s*\d+\s*[時間日分])$"
)


_SHORT_LABEL_WORDS = {
    "新品", "中古", "未使用", "傷あり", "終了",
    "急上昇", "注目", "即決", "現在", "落札",
    # Single-kanji condition-ish labels Yahoo sometimes shows alone:
    "本", "可", "良",
}


def _looks_like_promo_label(text: str) -> bool:
    """True for short button/badge text that masquerades as a title link.

    Length alone isn't enough to disqualify — many kaiju items have a
    bare 3-char title like ゴジラ / モスラ / ガメラ. So we drop the
    raw length cutoff and keep an explicit allow/deny list:
    - empty / whitespace → promo
    - 1 char → almost certainly a badge
    - exact match against known short labels (新品, 中古, 急上昇, ...) → promo
    - matches the broader _PROMO_LABEL_RE (New!!, ¥-suffix, "残り…") → promo
    Anything else passes, even if short.
    """
    t = text.strip()
    if not t:
        return True
    if len(t) == 1:
        return True
    if t in _SHORT_LABEL_WORDS:
        return True
    if _PROMO_LABEL_RE.match(t):
        return True
    return False


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
    except requests.RequestException as exc:
        # Catches RetryError (urllib3 max-retries on 5xx), TooManyRedirects,
        # and anything else requests raises. Without this, a category that
        # 500s past its offset cap propagates an unhandled RetryError up
        # through _paginate and kills the whole BFS, dropping every item
        # collected so far.
        raise ScraperError(f"Request failed for {url}: {exc}") from exc
    _check_blocked(resp)
    return BeautifulSoup(resp.text, "html.parser")


def _extract_item_id(url: str) -> Optional[str]:
    m = _AUCTION_HREF_RE.search(url)
    return m.group(1) if m else None


def _parse_yen(text: str) -> Optional[int]:
    cleaned = re.sub(r"[¥￥,円\s\xa5]", "", text)
    return int(cleaned) if cleaned.isdigit() else None


# Matches a price token in any of the forms Yahoo Japan uses on listing pages:
#   ¥3,500   ￥3,500   3,500円   即決 3,500 円   100,000円
# Captures the digit/comma part as group "a" or "b". Allows optional
# whitespace between the number and 円.
_PRICE_TOKEN_RE = re.compile(r"(?:[¥￥]\s*(?P<a>[\d,]+)|(?P<b>[\d,]+)\s*円)")


def _to_int(num_str: str) -> Optional[int]:
    raw = num_str.replace(",", "").replace(",", "").strip()
    return int(raw) if raw.isdigit() else None


def _extract_prices(text: str) -> tuple[Optional[int], Optional[int]]:
    """Return (current_price, buynow_price) parsed from container text.

    Yahoo formats prices either with a leading yen sign (¥3,500) or a
    trailing 円 (3,500円); we accept both. The first occurrence is the
    current price, the second (if any) is the Buy-Now price. Duplicate
    consecutive matches (e.g. when the yen sign appears next to the same
    number twice in markup) are deduped.
    """
    seen: list[int] = []
    for m in _PRICE_TOKEN_RE.finditer(text):
        n = _to_int(m.group("a") or m.group("b") or "")
        if n is None:
            continue
        if seen and seen[-1] == n:
            continue  # likely the same price re-rendered (e.g. ¥3,500 3,500円)
        seen.append(n)
        if len(seen) >= 2:
            break
    return (seen[0] if seen else None, seen[1] if len(seen) > 1 else None)


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

    anchors = soup.find_all("a", href=_AUCTION_HREF_RE)
    logger.debug("Found %d auction anchor tags on page.", len(anchors))

    # Each item card on Yahoo's listing has several anchors with the same
    # /auction/<id> href (image, title, "New!!" badge, …). Group them by
    # item_id so we can pick the best title candidate per item instead of
    # whichever anchor we hit first.
    anchors_by_id: dict[str, list] = {}
    for anchor in anchors:
        item_id = _extract_item_id(anchor.get("href", ""))
        if item_id:
            anchors_by_id.setdefault(item_id, []).append(anchor)

    for item_id, item_anchors in anchors_by_id.items():
        try:
            primary = item_anchors[0]
            href = primary.get("href", "")
            item_url = href if href.startswith("http") else urljoin("https://auctions.yahoo.co.jp", href)

            container = _item_container(primary)
            text = container.get_text(" ", strip=True)

            # Title: pick the longest non-promo candidate across this item's
            # anchors. Each anchor's candidate is its visible text or, if
            # empty, the alt text of the first image inside it.
            title = ""
            for anchor in item_anchors:
                cand = anchor.get_text(strip=True)
                if not cand:
                    img = anchor.find("img")
                    if img:
                        cand = img.get("alt", "")
                if not cand or _looks_like_promo_label(cand):
                    continue
                if len(cand) > len(title):
                    title = cand
            if not title:
                continue

            current_price, buynow_price = _extract_prices(text)

            # Bid count: Yahoo writes this as "5 入札", "入札5", "入札 5回",
            # "入札数5" etc. — accept any digit run on either side of 入札.
            bid_m = re.search(r"(?:入札[数件]?\s*(\d+)|(\d+)\s*入札)", text)
            bid_count = int(bid_m.group(1) or bid_m.group(2)) if bid_m else 0

            # Time remaining: "残り 2日", "残り5時間", "残り 30分" etc.
            time_m = re.search(r"残り\s*(\d+\s*(?:日|時間|分|秒)(?:\s*\d+\s*(?:時間|分|秒))?)", text)
            time_remaining = ("残り" + time_m.group(1).strip()) if time_m else None

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
    # Set to True when auccat causes a 404 so we stop sending it
    auccat_failed = False

    # Warm up session cookies; prime category context when filtering by category
    warm_session(session, category_id=category_id)

    for page in range(max_pages):
        offset = page * items_per_page + 1
        params: dict = {
            "p": keyword,
            "n": items_per_page,
            "b": offset,
        }
        if category_id and not auccat_failed:
            params["auccat"] = category_id

        logger.debug("Fetching page %d | params: %s", page + 1, params)

        try:
            soup = fetch_page(session, base_url, params)
        except RateLimitError:
            logger.warning("Rate limit hit on page %d, stopping early.", page + 1)
            break
        except ScraperError as exc:
            # If a category-filtered request returns 404, the stored category ID
            # is likely outdated.  Strip it, retry this page without the filter,
            # and continue — better to return unfiltered results than nothing.
            if "404" in str(exc) and params.get("auccat") and not auccat_failed:
                auccat_failed = True
                logger.warning(
                    "Category ID '%s' returned HTTP 404 — it may be outdated. "
                    "Run 'python main.py browse' to obtain a current category ID. "
                    "Retrying page %d without category filter...",
                    category_id, page + 1,
                )
                params.pop("auccat")
                try:
                    soup = fetch_page(session, base_url, params)
                except ScraperError as exc2:
                    logger.error("Scrape error on page %d: %s", page + 1, exc2)
                    break
            else:
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
