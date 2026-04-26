"""Interactive Yahoo Japan Auction category browser with local cache."""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .config import CATEGORIES, DEFAULT_SETTINGS

logger = logging.getLogger(__name__)

MAIN_PAGE_URL = "https://auctions.yahoo.co.jp/"
# Use the search URL with auccat= filter to get subcategory navigation links;
# /category/list/{id} returns 404 for most category IDs.
CATEGORY_PAGE_URL = "https://auctions.yahoo.co.jp/search/search?auccat={cat_id}"

# Top-level categories used to come from the main page, but Yahoo now renders
# that list via JS — only the "+条件指定" filter link is in the static HTML.
# These server-rendered URLs still include the full category list as
# ?auccat=… anchors, so we walk this chain in order and accept the first
# response that yields ≥ MIN_TOP_CATEGORIES sensible nodes.
TOP_CATEGORY_URL_CANDIDATES = (
    "https://auctions.yahoo.co.jp/search/search?p=",
    "https://auctions.yahoo.co.jp/category/list",
    "https://auctions.yahoo.co.jp/",  # last‑ditch fallback (current behavior)
)
MIN_TOP_CATEGORIES = 5

# Names that match _parse_links' selector but are not real categories — Yahoo
# wires several admin/filter links to ?auccat= or /list/ URLs.
_NOISE_NAME_SUBSTRINGS = ("条件指定", "ログイン", "ヘルプ", "条件を保存")
_NOISE_NAME_PREFIXES = ("＋", "+")

CACHE_TTL_DAYS = 7


@dataclass
class CategoryNode:
    id: str
    name: str
    children: list["CategoryNode"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "children": [c.to_dict() for c in self.children],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CategoryNode":
        return cls(
            id=d["id"],
            name=d["name"],
            children=[cls.from_dict(c) for c in d.get("children", [])],
        )


def _extract_cat_id(href: str) -> Optional[str]:
    m = re.search(r"auccat=(\d+)", href)
    if m:
        return m.group(1)
    m = re.search(r"/list/(\d+)", href)
    if m:
        return m.group(1)
    return None


def _is_noise_name(name: str) -> bool:
    """True for non-category links that share our auccat=/list/ selector."""
    if any(name.startswith(p) for p in _NOISE_NAME_PREFIXES):
        return True
    return any(s in name for s in _NOISE_NAME_SUBSTRINGS)


def _parse_links(soup: BeautifulSoup, exclude_id: Optional[str] = None) -> list[CategoryNode]:
    seen: set[str] = set()
    nodes: list[CategoryNode] = []

    for a in soup.select("a[href*='auccat='], a[href*='/list/']"):
        href = a.get("href", "")
        cat_id = _extract_cat_id(href)
        if not cat_id or cat_id in seen or cat_id == exclude_id:
            continue
        name = a.get_text(strip=True)
        if not name or len(name) > 60 or len(name) < 2:
            continue
        if _is_noise_name(name):
            continue
        seen.add(cat_id)
        nodes.append(CategoryNode(id=cat_id, name=name))

    return nodes


def _get(session: requests.Session, url: str) -> Optional[BeautifulSoup]:
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            logger.warning("HTTP %d fetching %s", resp.status_code, url)
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        logger.warning("Request failed for %s: %s", url, exc)
        return None


def fetch_top_categories(session: requests.Session) -> list[CategoryNode]:
    """Fetch Yahoo Japan's top-level categories.

    Yahoo's main page is JS-rendered; only the "+条件指定" filter link
    appears in static HTML. We try a small chain of server-rendered URLs
    and accept the first one whose parsed result looks healthy
    (≥ MIN_TOP_CATEGORIES nodes after noise filtering).
    """
    best: list[CategoryNode] = []
    for url in TOP_CATEGORY_URL_CANDIDATES:
        soup = _get(session, url)
        if soup is None:
            logger.info("Top-category fetch from %s: no response (HTTP error or timeout)", url)
            continue
        nodes = _parse_links(soup)
        logger.info(
            "Top-category fetch from %s: %d nodes after filter (sample: %s)",
            url, len(nodes), [n.name for n in nodes[:5]],
        )
        if len(nodes) >= MIN_TOP_CATEGORIES:
            return nodes
        # Keep the largest sub-threshold result as a degraded fallback.
        if len(nodes) > len(best):
            best = nodes
    if best:
        logger.warning(
            "All top-category URLs returned < %d nodes; using best (%d).",
            MIN_TOP_CATEGORIES, len(best),
        )
    else:
        logger.warning("All top-category URLs failed or returned no nodes.")
    return best


def fetch_subcategories(session: requests.Session, cat_id: str) -> list[CategoryNode]:
    url = CATEGORY_PAGE_URL.format(cat_id=cat_id)
    soup = _get(session, url)
    if soup is None:
        return []
    nodes = _parse_links(soup, exclude_id=cat_id)
    logger.info("Fetched %d subcategories for %s", len(nodes), cat_id)
    return nodes


def _normalize(name: str) -> str:
    """Match category names tolerantly: drop whitespace, treat ・ and 、 alike."""
    return name.replace("・", "、").replace(" ", "").replace("　", "").strip()


# Yahoo's tree's depth 0 is the auction main page itself ("オークショントップ"),
# which is not a clickable subcategory — fetch_top_categories already returns
# its depth-1 children. Paths that include the root are accepted and the
# leading element is silently skipped.
_ROOT_NAMES = {_normalize(n) for n in (
    "オークショントップ",
    "オークションTOP",
    "オークション トップ",
    "Auction Top",
)}


def resolve_path(
    session: requests.Session,
    names: list[str],
    *,
    cache_path: Optional[Path] = None,
) -> Optional[CategoryNode]:
    """Walk Yahoo Japan's live category tree top -> leaf by Japanese name.

    Returns the leaf CategoryNode (with .id and .name) or None on miss.
    Comparisons normalize ・ vs 、 and ignore whitespace, so callers can
    spell paths either way. A leading "オークショントップ" (the implicit
    depth-0 root) is accepted and skipped.
    """
    if not names:
        return None

    # Strip a leading "オークショントップ" — that's Yahoo's depth-0 root, which
    # is the auction main page rather than a clickable subcategory.
    walk_names = list(names)
    if walk_names and _normalize(walk_names[0]) in _ROOT_NAMES:
        walk_names = walk_names[1:]
    if not walk_names:
        return None

    # Use cached top-level if present and fresh, otherwise fetch live.
    top = load_cache(cache_path) if cache_path else None
    if top is None:
        top = fetch_top_categories(session)
        if top and cache_path is not None:
            save_cache(top, cache_path)
    if top is None:
        top = []

    # Always merge in _builtin_nodes() entries that aren't already present by
    # name. The live fetch can be partial (e.g. a candidate URL returns only a
    # subset of categories or only noise that gets filtered to a small list);
    # without the merge, well-known paths like `kaiju` fail at depth 0 because
    # おもちゃ、ゲーム happens not to be in the partial live result. The merge
    # is by Japanese name so a live entry with a fresher ID always wins.
    seen_names = {_normalize(n.name) for n in top}
    for hedge in _builtin_nodes():
        if _normalize(hedge.name) not in seen_names:
            top.append(hedge)
            seen_names.add(_normalize(hedge.name))

    if not top:
        return None

    targets = [_normalize(n) for n in walk_names]
    current_list = top
    matched: Optional[CategoryNode] = None

    for depth, target in enumerate(targets):
        match = next((n for n in current_list if _normalize(n.name) == target), None)
        if match is None:
            logger.warning(
                "resolve_path: no match for %r at depth %d (%d candidates: %s)",
                walk_names[depth], depth, len(current_list),
                [n.name for n in current_list][:12],
            )
            return None
        matched = match
        logger.debug("resolve_path: depth %d -> %s [%s]", depth, match.name, match.id)
        if depth < len(targets) - 1:
            current_list = fetch_subcategories(session, match.id)
            if not current_list:
                return None

    return matched


# Extra top-level categories that aren't in config.CATEGORIES but are needed
# by built-in shortcut paths (e.g. the `kaiju` command). These exist purely
# as a hedge — if every TOP_CATEGORY_URL_CANDIDATES request fails, name-based
# resolvers can still find the entry. The leaf walk past this point goes
# through fetch_subcategories, which uses the search page and is independent
# of the broken main-page DOM.
_EXTRA_TOP_NODES = (
    ("おもちゃ、ゲーム", "26146"),  # path root for `kaiju`
)


def _builtin_nodes() -> list[CategoryNode]:
    """Static fallback — built-in categories from config.py plus extras."""
    nodes = []
    seen_ids: set[str] = set()
    for cat in CATEGORIES.values():
        node = CategoryNode(id=cat["id"], name=cat["name_ja"])
        for sub in cat.get("subcategories", {}).values():
            node.children.append(CategoryNode(id=sub["id"], name=sub["name_ja"]))
        nodes.append(node)
        seen_ids.add(cat["id"])
    for name, cat_id in _EXTRA_TOP_NODES:
        if cat_id not in seen_ids:
            nodes.append(CategoryNode(id=cat_id, name=name))
            seen_ids.add(cat_id)
    return nodes


def load_cache(path: Path) -> Optional[list[CategoryNode]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data["cached_at"])
        if datetime.now() - cached_at > timedelta(days=CACHE_TTL_DAYS):
            return None
        nodes = [CategoryNode.from_dict(c) for c in data["categories"]]
        # Strip noise that older versions of fetch_top_categories may have
        # written ("+条件指定" et al.) and treat the cache as a miss if what's
        # left looks broken. Without this, a cache file written before the
        # noise-filter shipped will keep poisoning resolution forever.
        nodes = [n for n in nodes if not _is_noise_name(n.name)]
        if len(nodes) < MIN_TOP_CATEGORIES:
            logger.info(
                "Cached top-category list has only %d node(s) after noise filter — "
                "treating as miss and re-fetching.", len(nodes),
            )
            return None
        return nodes
    except Exception:
        return None


def save_cache(nodes: list[CategoryNode], path: Path) -> None:
    try:
        path.write_text(
            json.dumps(
                {"cached_at": datetime.now().isoformat(),
                 "categories": [n.to_dict() for n in nodes]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to save category cache: %s", exc)


def _normalize_name(name: str) -> str:
    """Loose normalization so live and static names compare equal across
    punctuation differences (・ vs 、 vs ／, full/half-width spaces)."""
    out = name
    for ch in "・、／/ 　":
        out = out.replace(ch, "")
    return out.strip().lower()


def _find_in_tree(nodes: list[CategoryNode], name_ja: str) -> Optional[CategoryNode]:
    target = _normalize_name(name_ja)
    for n in nodes:
        if _normalize_name(n.name) == target:
            return n
        hit = _find_in_tree(n.children, name_ja)
        if hit:
            return hit
    return None


def get_top_categories(
    session: requests.Session,
    cache_path: Path,
    *,
    force_refresh: bool = False,
) -> list[CategoryNode]:
    """Return the top-level category list, refreshing the cache if stale or missing."""
    if not force_refresh:
        cached = load_cache(cache_path)
        if cached is not None:
            return cached
    nodes = fetch_top_categories(session)
    if nodes:
        save_cache(nodes, cache_path)
    return nodes


def lookup_live_id(
    session: requests.Session,
    name_ja: str,
    cache_path: Path,
    *,
    parent_name_ja: Optional[str] = None,
) -> Optional[str]:
    """Look up the current live category ID for a Japanese category name.

    For top-level aliases, searches the cached top-level tree (refreshing if
    stale). For sub-aliases, also fetches the parent's subcategories on-demand
    and caches them under the parent node so the next call is offline.
    """
    top = get_top_categories(session, cache_path)
    if not top:
        return None

    # Top-level lookup
    if parent_name_ja is None:
        node = _find_in_tree(top, name_ja)
        return node.id if node else None

    # Sub-category lookup: find parent node first
    parent = _find_in_tree(top, parent_name_ja)
    if parent is None:
        return None
    # Drill down: prefer cached children, otherwise fetch and cache
    if not parent.children:
        parent.children = fetch_subcategories(session, parent.id)
        if parent.children:
            save_cache(top, cache_path)
    node = _find_in_tree(parent.children, name_ja)
    return node.id if node else None


def interactive_browse(
    session: requests.Session,
    cache_path: Path = Path("categories_cache.json"),
) -> Optional[str]:
    """
    Show a numbered category menu and let the user drill down level by level.
    Returns the selected category ID string, or None if cancelled.
    """
    import click

    # Always visit the main page first so session cookies are set.
    # Without this, fetch_subcategories fails on every run that loads
    # top categories from cache (cache hit skips fetch_top_categories,
    # which was the only place that set cookies).
    try:
        session.get(MAIN_PAGE_URL, timeout=15)
    except Exception:
        pass

    top_nodes = load_cache(cache_path)
    if top_nodes is None:
        click.echo("Fetching categories from Yahoo Japan Auctions...")
        top_nodes = fetch_top_categories(session)
        if top_nodes:
            save_cache(top_nodes, cache_path)
        else:
            click.echo("Could not fetch live categories. Using built-in list.")
            top_nodes = _builtin_nodes()

    current: list[CategoryNode] = top_nodes
    breadcrumb: list[CategoryNode] = []
    # Stack of previous `current` lists; lets B restore the exact list that
    # was shown at each level without re-fetching over the network.
    history: list[list[CategoryNode]] = []

    while True:
        # Header
        if breadcrumb:
            header = " > ".join(n.name for n in breadcrumb)
        else:
            header = "Yahoo Japan Auction Categories"
        click.echo(f"\n{'=' * 50}")
        click.echo(f"  {header}")
        click.echo(f"{'=' * 50}")

        for i, node in enumerate(current, 1):
            click.echo(f"  {i:3d}.  {node.name}  [{node.id}]")

        click.echo("")
        if breadcrumb:
            click.echo(f"    0.  ✓ Use \"{breadcrumb[-1].name}\" (ID: {breadcrumb[-1].id})")
            click.echo("    B.  ← Go back")
        else:
            click.echo("    0.  Cancel")

        raw = click.prompt("\nSelect", default="0").strip()

        if raw.upper() == "B":
            if breadcrumb:
                breadcrumb.pop()
                current = history.pop() if history else top_nodes
            continue

        if raw == "0":
            if breadcrumb:
                node = breadcrumb[-1]
                _print_selection(breadcrumb)
                return node.id
            click.echo("Cancelled.")
            return None

        try:
            idx = int(raw) - 1
        except ValueError:
            click.echo("Please enter a number.")
            continue

        if not (0 <= idx < len(current)):
            click.echo(f"Please enter a number between 0 and {len(current)}.")
            continue

        chosen = current[idx]
        breadcrumb.append(chosen)
        click.echo(f"\nFetching subcategories for \"{chosen.name}\"...")
        subs = fetch_subcategories(session, chosen.id)
        time.sleep(DEFAULT_SETTINGS["request_delay_seconds"])

        if subs:
            history.append(current)
            current = subs
        else:
            # Leaf node — no subcategories
            _print_selection(breadcrumb)
            return chosen.id


def _print_selection(breadcrumb: list[CategoryNode]) -> None:
    import click
    path = " > ".join(n.name for n in breadcrumb)
    node = breadcrumb[-1]
    click.echo(f"\nSelected: {path}")
    click.echo(f"Category ID: {node.id}")
    click.echo(f"\nTip: use with other commands:  --category {node.id}")
