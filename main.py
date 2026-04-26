#!/usr/bin/env python3
from __future__ import annotations

import csv
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import click
from tabulate import tabulate

from yahoo_auction_tracker.config import (
    CATEGORIES,
    DEFAULT_SETTINGS,
    CATEGORY_ID_TO_NAME,
    get_name_ja,
    get_parent_alias,
    resolve_category,
)
from yahoo_auction_tracker.database import (
    delete_search,
    get_latest_prices,
    get_price_history,
    get_price_summary,
    init_db,
    list_searches,
)
from yahoo_auction_tracker.scraper import (
    RateLimitError,
    ScraperError,
    build_session,
    search_active,
    search_closed,
)
from yahoo_auction_tracker.category_browser import interactive_browse, lookup_live_id
from yahoo_auction_tracker.html_report import generate_html
from yahoo_auction_tracker.tracker import AuctionTracker, get_japan_time


def _truncate(text: str, width: int = 45) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _fmt_price(price: Optional[int]) -> str:
    if price is None:
        return "-"
    return f"¥{price:,}"


def _resolve_or_exit(category: Optional[str]) -> Optional[str]:
    if not category:
        return None
    cat_id = resolve_category(category)
    if cat_id is None:
        click.echo(
            click.style(
                f"Error: unknown category '{category}'. "
                "Use 'list-categories' to see valid aliases.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(1)
    return cat_id


def _refresh_alias_id(
    category: Optional[str],
    static_id: Optional[str],
    *,
    cache_path: str = "categories_cache.json",
) -> Optional[str]:
    """If `category` is an alias (not a numeric ID), refresh its ID against the
    live Yahoo Japan tree and return the current ID. Falls back to `static_id`
    on any lookup failure so a network blip never blocks a search.

    The lookup uses the local `categories_cache.json` (auto-refreshed when
    older than 7 days) so the cost on the warm path is a single file read.
    """
    if not category or not static_id:
        return static_id
    if category.isdigit():
        return static_id
    name_ja = get_name_ja(category)
    if not name_ja:
        return static_id
    parent_alias = get_parent_alias(category)
    parent_name_ja = get_name_ja(parent_alias) if parent_alias and parent_alias != category else None
    try:
        # Late import keeps category_browser's requests/bs4 deps off the cold path
        from yahoo_auction_tracker.scraper import build_session
        session = build_session()
        try:
            live_id = lookup_live_id(
                session, name_ja, Path(cache_path),
                parent_name_ja=parent_name_ja,
            )
        finally:
            session.close()
    except Exception as exc:
        logging.getLogger(__name__).debug("Live category lookup failed: %s", exc)
        return static_id
    if live_id and live_id != static_id:
        click.echo(
            click.style(
                f"Note: category '{category}' ID refreshed {static_id} → {live_id}.",
                fg="cyan",
            ),
            err=True,
        )
        return live_id
    return static_id


def _prompt_category() -> Optional[str]:
    """Show a numbered list of top-level categories and return the chosen ID.

    Called when --category is omitted in interactive use. The user can:
    - Pick a number to select a top-level category
    - Type a raw category ID or alias directly
    - Press Enter (or type 0) to search without a category filter
    - Type 'B' to be reminded to use the 'browse' command for sub-categories
    """
    cats = list(CATEGORIES.items())
    click.echo("\nAvailable top-level categories:")
    for i, (alias, cat) in enumerate(cats, 1):
        click.echo(f"  {i:2d}.  {cat['name_ja']}  [{alias}]")
    click.echo("\n   0.  No category filter (search all)")
    click.echo("   B.  Browse full sub-category tree  →  run: python main.py browse")

    raw = click.prompt("\nCategory", default="0").strip()

    if raw == "0" or raw == "":
        return None
    if raw.upper() == "B":
        click.echo("Run  python main.py browse  to browse sub-categories, then rerun with --category <ID>.")
        sys.exit(0)
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(cats):
            return list(CATEGORIES.values())[idx]["id"]
    except ValueError:
        pass
    # Treat as alias or raw numeric ID
    result = resolve_category(raw)
    if result is None:
        click.echo(f"Unknown category '{raw}', proceeding without category filter.")
    return result


@click.group()
@click.option("--db", default=None, help="Path to SQLite database (default: auction_tracker.db)")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.pass_context
def cli(ctx: click.Context, db: Optional[str], verbose: bool) -> None:
    """Yahoo Japan Auction Price Tracker

    Track and analyse auction prices by keyword and category.
    Japan Standard Time is fetched from worldtimeapi.org for accurate daily snapshots.
    """
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db or DEFAULT_SETTINGS["db_path"]
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s" if verbose else "%(message)s",
    )


# ---------------------------------------------------------------------------
# list-categories
# ---------------------------------------------------------------------------

@cli.command("list-categories")
def list_categories() -> None:
    """Show all supported category aliases and their IDs."""
    rows = []
    for alias, cat in CATEGORIES.items():
        rows.append([alias, cat["id"], cat["name_ja"], ""])
        for sub_alias, sub in cat.get("subcategories", {}).items():
            rows.append(["", sub["id"], sub["name_ja"], f"  └ {sub_alias}"])
    click.echo(tabulate(rows, headers=["Alias", "Category ID", "日本語名", "Sub-alias"]))


# ---------------------------------------------------------------------------
# browse (interactive category selector)
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--cache", default="categories_cache.json", show_default=True,
              help="Path to local category cache file")
@click.pass_context
def browse(ctx: click.Context, cache: str) -> None:
    """Interactively browse Yahoo Japan auction categories.

    Fetches the live category tree from Yahoo Japan, lets you drill down
    through subcategories with a numbered menu, and prints the selected
    category ID so you can use it with search, track, or report.

    The category tree is cached locally for 7 days to avoid repeated fetches.
    """
    session = build_session()
    try:
        interactive_browse(session, cache_path=Path(cache))
    except (RateLimitError, ScraperError) as exc:
        click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
        sys.exit(1)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# japan-time
# ---------------------------------------------------------------------------

@cli.command("japan-time")
def japan_time_cmd() -> None:
    """Show the current Japan Standard Time fetched from worldtimeapi.org."""
    jst = get_japan_time()
    click.echo(f"Japan Standard Time (JST): {jst.strftime('%Y-%m-%d %H:%M:%S %Z%z')}")


# ---------------------------------------------------------------------------
# search (no DB write)
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--keyword", "-k", default=None, prompt="Search keyword", help="Search keyword")
@click.option("--category", "-c", default=None, help="Category alias or numeric ID")
@click.option("--closed", is_flag=True, default=False, help="Search closed/sold auctions")
@click.option("--pages", default=2, show_default=True, help="Pages to fetch (1–5)")
@click.pass_context
def search(
    ctx: click.Context,
    keyword: str,
    category: Optional[str],
    closed: bool,
    pages: int,
) -> None:
    """Search auctions and display results (does not save to database)."""
    cat_id = _resolve_or_exit(category) if category is not None else _prompt_category()
    cat_id = _refresh_alias_id(category, cat_id)
    pages = max(1, min(pages, 5))
    session = build_session()
    try:
        fn = search_closed if closed else search_active
        items = fn(session, keyword, cat_id, max_pages=pages)
    except RateLimitError as exc:
        click.echo(click.style(f"Rate limit: {exc}", fg="red"), err=True)
        sys.exit(1)
    except ScraperError as exc:
        click.echo(click.style(f"Scraper error: {exc}", fg="red"), err=True)
        sys.exit(1)
    finally:
        session.close()

    if not items:
        click.echo("No results found.")
        if cat_id:
            click.echo(
                click.style(
                    f"Tip: category ID '{cat_id}' may be outdated. "
                    "Run 'python main.py browse' to get a current ID.",
                    fg="yellow",
                )
            )
        return

    rows = [
        [
            _truncate(i.title),
            _fmt_price(i.current_price),
            _fmt_price(i.buynow_price),
            i.bid_count,
            i.time_remaining or "-",
            i.condition or "-",
        ]
        for i in items
    ]
    click.echo(
        tabulate(
            rows,
            headers=["Title", "Price", "Buy-Now", "Bids", "Time Remaining", "Condition"],
            tablefmt="rounded_outline",
        )
    )
    click.echo(f"\nTotal: {len(items)} items")


# ---------------------------------------------------------------------------
# track (one-shot scrape + save)
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--keyword", "-k", default=None, prompt="Search keyword")
@click.option("--category", "-c", default=None, help="Category alias or numeric ID")
@click.option("--no-closed", is_flag=True, default=False, help="Skip closed auction search")
@click.pass_context
def track(ctx: click.Context, keyword: str, category: Optional[str], no_closed: bool) -> None:
    """Run a one-time update: scrape auctions and save prices to database."""
    cat_id = _resolve_or_exit(category) if category is not None else _prompt_category()
    cat_id = _refresh_alias_id(category, cat_id)
    tracker = AuctionTracker(db_path=ctx.obj["db_path"])
    try:
        result = tracker.run_daily_update(keyword, cat_id, include_closed=not no_closed)
    finally:
        tracker.close()

    rows = [
        ["Snapshot date", result["snapshot_date"]],
        ["Keyword", result["keyword"]],
        ["Category ID", result["category_id"] or "any"],
        ["Active items fetched", result["active_count"]],
        ["Closed items fetched", result["closed_count"]],
        ["New items saved", result["new_items"]],
        ["Items updated", result["updated_items"]],
    ]
    click.echo(tabulate(rows, tablefmt="rounded_outline"))

    if result["errors"]:
        click.echo(click.style("\nWarnings:", fg="yellow"))
        for err in result["errors"]:
            click.echo(f"  • {err}")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--keyword", "-k", default=None, prompt="Search keyword")
@click.option("--category", "-c", default=None)
@click.option("--days", default=30, show_default=True, help="Number of days of history to show")
@click.option(
    "--format", "fmt",
    type=click.Choice(["table", "csv", "html"]),
    default="table",
    show_default=True,
)
@click.option(
    "--output", "-o", default=None,
    help="Output file path for html/csv format (default: <keyword>_report.html or stdout)",
)
@click.pass_context
def report(
    ctx: click.Context,
    keyword: str,
    category: Optional[str],
    days: int,
    fmt: str,
    output: Optional[str],
) -> None:
    """Show price history and trend summary for a keyword.

    Use --format html to generate a standalone HTML report file with a price chart.
    """
    cat_id = _resolve_or_exit(category) if category is not None else _prompt_category()

    # Report is read-only by design. If there's no database yet, say so
    # instead of silently creating one — and let any other OperationalError
    # surface so corruption / permission issues aren't masked.
    db_path = ctx.obj["db_path"]
    if not Path(db_path).exists():
        click.echo(f"No database at '{db_path}'. Run 'python main.py track' first.")
        return

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        summary = get_price_summary(conn, keyword, cat_id, days=days)
        history = get_price_history(conn, keyword, cat_id, days=days)
    finally:
        conn.close()

    if fmt == "html":
        safe_keyword = "".join(c if c.isalnum() else "_" for c in keyword)
        out_path = Path(output) if output else Path(f"{safe_keyword}_report.html")
        html_content = generate_html(
            summary,
            history,
            keyword=keyword,
            category_id=cat_id,
            days=days,
        )
        out_path.write_text(html_content, encoding="utf-8")
        click.echo(f"HTML report saved to: {out_path.resolve()}")
        return

    if fmt == "table":
        trend_color = {"up": "red", "down": "green", "stable": "cyan"}.get(
            summary["price_trend"], "white"
        )
        click.echo(click.style(f"\n=== Price Summary: {keyword} ===", bold=True))
        summary_rows = [
            ["Period", f"{days} days"],
            ["Items tracked", summary["item_count"]],
            ["Snapshots", summary["snapshot_count"]],
            ["Avg price", _fmt_price(int(summary["avg_price"])) if summary["avg_price"] else "-"],
            ["Min price", _fmt_price(summary["min_price"])],
            ["Max price", _fmt_price(summary["max_price"])],
            ["Median price", _fmt_price(int(summary["median_price"])) if summary["median_price"] else "-"],
            ["Avg bids", summary["avg_bid_count"] or "-"],
            ["Trend", click.style(summary["price_trend"], fg=trend_color)],
        ]
        click.echo(tabulate(summary_rows, tablefmt="rounded_outline"))

        if history:
            click.echo(click.style("\n=== Price History ===", bold=True))
            h_rows = [
                [
                    r["snapshot_date"],
                    _truncate(r["title"], 35),
                    _fmt_price(r["current_price"]),
                    _fmt_price(r["buynow_price"]),
                    r["bid_count"],
                    r["condition"] or "-",
                    "closed" if r["is_closed"] else "active",
                ]
                for r in history
            ]
            click.echo(
                tabulate(
                    h_rows,
                    headers=["Date", "Title", "Price", "Buy-Now", "Bids", "Condition", "Status"],
                    tablefmt="rounded_outline",
                )
            )
        else:
            click.echo("\nNo history found. Run 'track' first.")

    else:  # csv
        dest = open(output, "w", newline="", encoding="utf-8") if output else sys.stdout
        try:
            writer = csv.writer(dest)
            writer.writerow(["date", "item_id", "title", "current_price", "buynow_price",
                             "bid_count", "condition", "is_closed", "item_url"])
            for r in history:
                writer.writerow([
                    r["snapshot_date"], r["item_id"], r["title"],
                    r["current_price"] or "", r["buynow_price"] or "",
                    r["bid_count"], r["condition"] or "", r["is_closed"],
                    r["item_url"],
                ])
        finally:
            if output:
                dest.close()
                click.echo(f"CSV saved to: {Path(output).resolve()}")


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--keyword", "-k", default=None, prompt="Search keyword")
@click.option("--category", "-c", default=None)
@click.option("--time", "run_time", default="09:00", show_default=True,
              help="Daily run time HH:MM in Japan Standard Time")
@click.option("--run-now", is_flag=True, default=False,
              help="Run an immediate update before entering the daily schedule")
@click.option("--no-closed", is_flag=True, default=False)
@click.pass_context
def schedule(
    ctx: click.Context,
    keyword: str,
    category: Optional[str],
    run_time: str,
    run_now: bool,
    no_closed: bool,
) -> None:
    """Schedule daily price tracking (blocks until Ctrl+C)."""
    import re as _re
    if not _re.match(r"^\d{2}:\d{2}$", run_time):
        click.echo(click.style("Error: --time must be in HH:MM format", fg="red"), err=True)
        sys.exit(1)

    static_cat_id = _resolve_or_exit(category) if category is not None else _prompt_category()
    cat_id = _refresh_alias_id(category, static_cat_id)
    tracker = AuctionTracker(db_path=ctx.obj["db_path"])

    # Refresh the live ID before every scheduled run, not just at startup, so
    # a long-running schedule self-heals when Yahoo Japan changes IDs.
    if category and not category.isdigit():
        def _per_run_resolver() -> Optional[str]:
            return _refresh_alias_id(category, static_cat_id)
    else:
        _per_run_resolver = None  # type: ignore[assignment]

    jst = get_japan_time()
    click.echo(f"Current Japan time: {jst.strftime('%Y-%m-%d %H:%M:%S JST')}")
    click.echo(f"Scheduled daily update for '{keyword}' at {run_time} JST.")
    click.echo("Press Ctrl+C to stop.\n")

    try:
        tracker.schedule_daily(
            keyword,
            cat_id,
            run_time=run_time,
            include_closed=not no_closed,
            run_now=run_now,
            resolve_category_id=_per_run_resolver,
        )
    finally:
        tracker.close()


# ---------------------------------------------------------------------------
# list (tracked searches)
# ---------------------------------------------------------------------------

@cli.command("list")
@click.pass_context
def list_cmd(ctx: click.Context) -> None:
    """List all tracked keyword/category configurations."""
    conn = init_db(ctx.obj["db_path"])
    try:
        searches = list_searches(conn)
    finally:
        conn.close()

    if not searches:
        click.echo("No tracked searches yet. Run 'track' to start.")
        return

    rows = [
        [
            s["id"],
            s["keyword"],
            CATEGORY_ID_TO_NAME.get(s["category_id"] or "", s["category_id"] or "any"),
            s["last_run_at"] or "never",
            s["item_count"],
        ]
        for s in searches
    ]
    click.echo(
        tabulate(
            rows,
            headers=["ID", "Keyword", "Category", "Last Run (UTC)", "Items"],
            tablefmt="rounded_outline",
        )
    )


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("search_id", type=int)
@click.pass_context
def delete(ctx: click.Context, search_id: int) -> None:
    """Delete a tracked search and all its history by ID."""
    conn = init_db(ctx.obj["db_path"])
    try:
        removed = delete_search(conn, search_id)
    finally:
        conn.close()
    if removed == 0:
        click.echo(click.style(f"No search with ID {search_id} found.", fg="red"), err=True)
        sys.exit(1)
    click.echo(f"Deleted search ID {search_id} and all associated data.")


if __name__ == "__main__":
    cli()
