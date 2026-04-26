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
    soup = _get(session, MAIN_PAGE_URL)
    if soup is None:
        return []
    nodes = _parse_links(soup)
    logger.info("Fetched %d top-level categories", len(nodes))
    return nodes


def fetch_subcategories(session: requests.Session, cat_id: str) -> list[CategoryNode]:
    url = CATEGORY_PAGE_URL.format(cat_id=cat_id)
    soup = _get(session, url)
    if soup is None:
        return []
    nodes = _parse_links(soup, exclude_id=cat_id)
    logger.info("Fetched %d subcategories for %s", len(nodes), cat_id)
    return nodes


def _builtin_nodes() -> list[CategoryNode]:
    """Static fallback — built-in categories from config.py."""
    nodes = []
    for cat in CATEGORIES.values():
        node = CategoryNode(id=cat["id"], name=cat["name_ja"])
        for sub in cat.get("subcategories", {}).values():
            node.children.append(CategoryNode(id=sub["id"], name=sub["name_ja"]))
        nodes.append(node)
    return nodes


def load_cache(path: Path) -> Optional[list[CategoryNode]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(data["cached_at"])
        if datetime.now() - cached_at > timedelta(days=CACHE_TTL_DAYS):
            return None
        return [CategoryNode.from_dict(c) for c in data["categories"]]
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
