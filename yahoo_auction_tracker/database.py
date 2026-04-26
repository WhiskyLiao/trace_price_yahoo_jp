from __future__ import annotations

import sqlite3
import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from .config import DEFAULT_SETTINGS

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS searches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword      TEXT    NOT NULL,
    category_id  TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    last_run_at  TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1,
    UNIQUE(keyword, category_id)
);

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id     INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    item_id       TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    item_url      TEXT    NOT NULL,
    seller_id     TEXT,
    condition     TEXT,
    first_seen_at TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(item_id)
);

CREATE TABLE IF NOT EXISTS price_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id        TEXT    NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
    search_id      INTEGER NOT NULL REFERENCES searches(id)  ON DELETE CASCADE,
    snapshot_date  TEXT    NOT NULL,
    current_price  INTEGER,
    buynow_price   INTEGER,
    bid_count      INTEGER DEFAULT 0,
    time_remaining TEXT,
    is_closed      INTEGER NOT NULL DEFAULT 0,
    scraped_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(item_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_ph_item   ON price_history(item_id);
CREATE INDEX IF NOT EXISTS idx_ph_date   ON price_history(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_ph_search ON price_history(search_id);
CREATE INDEX IF NOT EXISTS idx_items_search ON items(search_id);
"""


def init_db(db_path: str = DEFAULT_SETTINGS["db_path"]) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    conn.commit()
    return conn


def _cat(category_id: Optional[str]) -> str:
    return category_id or ""


def get_or_create_search(
    conn: sqlite3.Connection,
    keyword: str,
    category_id: Optional[str],
) -> int:
    cat = _cat(category_id)
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO searches (keyword, category_id) VALUES (?, ?)",
            (keyword, cat),
        )
        conn.execute(
            "UPDATE searches SET last_run_at = datetime('now') "
            "WHERE keyword = ? AND category_id = ?",
            (keyword, cat),
        )
    row = conn.execute(
        "SELECT id FROM searches WHERE keyword = ? AND category_id = ?",
        (keyword, cat),
    ).fetchone()
    return row["id"]


def save_items(
    conn: sqlite3.Connection,
    search_id: int,
    items: list,
    snapshot_date: Optional[str] = None,
) -> tuple[int, int]:
    if snapshot_date is None:
        # Default to JST so daily snapshots match the date in Japan even when
        # the host clock is in a different timezone. Imported lazily to avoid
        # a circular import (tracker imports database).
        from .tracker import get_japan_date_str
        snapshot_date = get_japan_date_str()

    inserted = updated = 0
    with conn:
        for item in items:
            # Upsert into items table
            existing = conn.execute(
                "SELECT id FROM items WHERE item_id = ?", (item.item_id,)
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO items (search_id, item_id, title, item_url, seller_id, condition) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (search_id, item.item_id, item.title, item.item_url,
                     item.seller_id, item.condition),
                )
                inserted += 1
            else:
                conn.execute(
                    "UPDATE items SET last_seen_at = datetime('now'), title = ?, "
                    "seller_id = ?, condition = ? WHERE item_id = ?",
                    (item.title, item.seller_id, item.condition, item.item_id),
                )
                updated += 1

            # Upsert daily snapshot
            conn.execute(
                "INSERT OR REPLACE INTO price_history "
                "(item_id, search_id, snapshot_date, current_price, buynow_price, "
                " bid_count, time_remaining, is_closed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.item_id, search_id, snapshot_date,
                    item.current_price, item.buynow_price,
                    item.bid_count, item.time_remaining,
                    1 if item.is_closed else 0,
                ),
            )
    return inserted, updated


def get_price_history(
    conn: sqlite3.Connection,
    keyword: str,
    category_id: Optional[str] = None,
    *,
    days: int = 30,
    item_id: Optional[str] = None,
) -> list[sqlite3.Row]:
    since = (date.today() - timedelta(days=days)).isoformat()
    query = """
        SELECT ph.snapshot_date, ph.current_price, ph.buynow_price,
               ph.bid_count, ph.time_remaining, ph.is_closed,
               i.item_id, i.title, i.item_url, i.condition, i.seller_id
        FROM price_history ph
        JOIN items i ON ph.item_id = i.item_id
        JOIN searches s ON ph.search_id = s.id
        WHERE s.keyword = ?
          AND s.category_id = ?
          AND ph.snapshot_date >= ?
    """
    params: list = [keyword, _cat(category_id), since]
    if item_id:
        query += " AND ph.item_id = ?"
        params.append(item_id)
    query += " ORDER BY i.item_id ASC, ph.snapshot_date ASC"
    return conn.execute(query, params).fetchall()


def get_latest_prices(
    conn: sqlite3.Connection,
    keyword: str,
    category_id: Optional[str] = None,
    *,
    limit: int = 50,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT ph.snapshot_date, ph.current_price, ph.buynow_price,
               ph.bid_count, ph.time_remaining, ph.is_closed,
               i.item_id, i.title, i.item_url, i.condition, i.seller_id
        FROM price_history ph
        JOIN items i ON ph.item_id = i.item_id
        JOIN searches s ON ph.search_id = s.id
        WHERE s.keyword = ?
          AND s.category_id = ?
          AND ph.snapshot_date = (
              SELECT MAX(ph2.snapshot_date) FROM price_history ph2
              WHERE ph2.item_id = ph.item_id
          )
        ORDER BY ph.current_price ASC NULLS LAST
        LIMIT ?
        """,
        (keyword, _cat(category_id), limit),
    ).fetchall()


def get_price_summary(
    conn: sqlite3.Connection,
    keyword: str,
    category_id: Optional[str] = None,
    *,
    days: int = 30,
) -> dict:
    rows = get_price_history(conn, keyword, category_id, days=days)
    prices = [r["current_price"] for r in rows if r["current_price"] is not None]
    bids = [r["bid_count"] for r in rows if r["bid_count"] is not None]

    # Trend is computed on per-date average prices, not on the raw row list.
    # The raw rows are ordered by item_id then date, so slicing them in half
    # would split by item rather than by time and skew the result toward
    # whichever items happen to be cheaper.
    daily: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        if r["current_price"] is not None:
            daily[r["snapshot_date"]].append(r["current_price"])
    daily_avgs = [sum(v) / len(v) for _, v in sorted(daily.items())]

    trend = "insufficient_data"
    if len(daily_avgs) >= 4:
        mid = len(daily_avgs) // 2
        first_avg = statistics.mean(daily_avgs[:mid])
        second_avg = statistics.mean(daily_avgs[mid:])
        diff_pct = (second_avg - first_avg) / first_avg if first_avg else 0
        if diff_pct > 0.03:
            trend = "up"
        elif diff_pct < -0.03:
            trend = "down"
        else:
            trend = "stable"

    return {
        "keyword": keyword,
        "category_id": category_id,
        "period_days": days,
        "item_count": len({r["item_id"] for r in rows}),
        "snapshot_count": len(rows),
        "avg_price": round(statistics.mean(prices), 1) if prices else None,
        "min_price": min(prices) if prices else None,
        "max_price": max(prices) if prices else None,
        "median_price": statistics.median(prices) if prices else None,
        "avg_bid_count": round(statistics.mean(bids), 1) if bids else None,
        "price_trend": trend,
    }


def list_searches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT s.id, s.keyword, s.category_id, s.last_run_at,
               COUNT(DISTINCT i.item_id) AS item_count
        FROM searches s
        LEFT JOIN items i ON i.search_id = s.id
        GROUP BY s.id
        ORDER BY s.last_run_at DESC
        """
    ).fetchall()


def delete_search(conn: sqlite3.Connection, search_id: int) -> int:
    """Delete a search row by id and return the number of rows removed."""
    with conn:
        cur = conn.execute("DELETE FROM searches WHERE id = ?", (search_id,))
    return cur.rowcount
