"""Generate a self-contained HTML price-history report."""
from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime
from typing import Optional


def _fmt_yen(price: Optional[int]) -> str:
    if price is None:
        return "—"
    return f"¥{price:,}"


def _trend_badge(trend: str) -> str:
    styles = {
        "up":                "background:#fee2e2;color:#991b1b",
        "down":              "background:#dcfce7;color:#166534",
        "stable":            "background:#e0f2fe;color:#075985",
        "insufficient_data": "background:#f3f4f6;color:#374151",
    }
    labels = {
        "up": "↑ Rising", "down": "↓ Falling",
        "stable": "→ Stable", "insufficient_data": "— Not enough data",
    }
    style = styles.get(trend, styles["insufficient_data"])
    label = labels.get(trend, trend)
    return f'<span style="padding:3px 10px;border-radius:99px;font-weight:600;font-size:.85em;{style}">{label}</span>'


def _condition_badge(cond: Optional[str]) -> str:
    if cond == "new":
        return '<span style="background:#d1fae5;color:#065f46;padding:1px 8px;border-radius:99px;font-size:.8em">New</span>'
    if cond == "used":
        return '<span style="background:#fef3c7;color:#92400e;padding:1px 8px;border-radius:99px;font-size:.8em">Used</span>'
    return "—"


def _status_badge(is_closed: int) -> str:
    if is_closed:
        return '<span style="background:#f3f4f6;color:#6b7280;padding:1px 8px;border-radius:99px;font-size:.8em">Sold</span>'
    return '<span style="background:#dbeafe;color:#1e40af;padding:1px 8px;border-radius:99px;font-size:.8em">Active</span>'


def build_chart_data(history: list) -> tuple[list[str], list[float]]:
    """Aggregate average daily price across all items, sorted by date."""
    daily: dict[str, list[int]] = defaultdict(list)
    for row in history:
        if row["current_price"] is not None:
            daily[row["snapshot_date"]].append(row["current_price"])
    dates = sorted(daily.keys())
    averages = [round(sum(daily[d]) / len(daily[d])) for d in dates]
    return dates, averages


def generate_html(
    summary: dict,
    history: list,
    *,
    keyword: str,
    category_id: Optional[str],
    days: int,
    generated_at: Optional[str] = None,
) -> str:
    if generated_at is None:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    category_label = category_id or "All categories"
    trend_html = _trend_badge(summary["price_trend"])

    # Chart data
    dates, avg_prices = build_chart_data(history)
    chart_labels = json.dumps(dates)
    chart_data = json.dumps(avg_prices)
    has_chart = len(dates) >= 2

    # Build history table rows
    table_rows = ""
    for row in history:
        esc_title = html.escape(row["title"] or "")
        esc_url = html.escape(row["item_url"] or "#")
        table_rows += f"""
        <tr>
          <td>{html.escape(row['snapshot_date'])}</td>
          <td class="title-cell"><a href="{esc_url}" target="_blank" rel="noopener">{esc_title}</a></td>
          <td class="num">{_fmt_yen(row['current_price'])}</td>
          <td class="num">{_fmt_yen(row['buynow_price'])}</td>
          <td class="num">{row['bid_count'] or 0}</td>
          <td>{_condition_badge(row['condition'])}</td>
          <td>{_status_badge(row['is_closed'])}</td>
        </tr>"""

    no_data_msg = "" if history else '<p class="no-data">No history found. Run <code>track</code> first to collect data.</p>'

    chart_section = ""
    if has_chart:
        chart_section = f"""
      <div class="card">
        <h2>Average Daily Price</h2>
        <div style="position:relative;height:320px">
          <canvas id="priceChart"></canvas>
        </div>
      </div>"""

    chart_script = ""
    if has_chart:
        chart_script = f"""
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <script>
      const ctx = document.getElementById('priceChart').getContext('2d');
      new Chart(ctx, {{
        type: 'line',
        data: {{
          labels: {chart_labels},
          datasets: [{{
            label: 'Avg Price (¥)',
            data: {chart_data},
            borderColor: '#2563eb',
            backgroundColor: 'rgba(37,99,235,0.08)',
            borderWidth: 2,
            pointRadius: 4,
            pointHoverRadius: 6,
            fill: true,
            tension: 0.3
          }}]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          plugins: {{
            legend: {{ display: false }},
            tooltip: {{
              callbacks: {{
                label: ctx => '¥' + ctx.parsed.y.toLocaleString()
              }}
            }}
          }},
          scales: {{
            y: {{
              ticks: {{
                callback: v => '¥' + v.toLocaleString()
              }}
            }}
          }}
        }}
      }});
    </script>"""

    avg_str = _fmt_yen(int(summary["avg_price"])) if summary["avg_price"] is not None else "—"
    median_str = _fmt_yen(int(summary["median_price"])) if summary["median_price"] is not None else "—"
    avg_bids = summary["avg_bid_count"] if summary["avg_bid_count"] is not None else "—"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Price Report — {html.escape(keyword)}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f8fafc;
      color: #1e293b;
      padding: 2rem 1rem;
    }}
    .container {{ max-width: 1100px; margin: 0 auto; }}
    header {{ margin-bottom: 2rem; }}
    header h1 {{ font-size: 1.75rem; font-weight: 700; color: #0f172a; }}
    header p {{ color: #64748b; margin-top: .3rem; font-size: .95rem; }}
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    .stat {{
      background: #fff;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      padding: 1.1rem 1.25rem;
    }}
    .stat .label {{ font-size: .75rem; font-weight: 600; text-transform: uppercase;
                    letter-spacing: .05em; color: #94a3b8; margin-bottom: .3rem; }}
    .stat .value {{ font-size: 1.4rem; font-weight: 700; color: #0f172a; }}
    .stat .value.yen {{ color: #2563eb; }}
    .card {{
      background: #fff;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
    }}
    .card h2 {{ font-size: 1.1rem; font-weight: 600; color: #0f172a; margin-bottom: 1rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .875rem; }}
    thead tr {{ background: #f1f5f9; }}
    th {{
      text-align: left;
      padding: .65rem .9rem;
      font-size: .75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: .05em;
      color: #64748b;
      white-space: nowrap;
    }}
    td {{ padding: .6rem .9rem; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #f8fafc; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    td.title-cell {{ max-width: 320px; }}
    td.title-cell a {{ color: #2563eb; text-decoration: none; display: block;
                        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    td.title-cell a:hover {{ text-decoration: underline; }}
    .no-data {{ color: #94a3b8; font-style: italic; padding: 1rem 0; }}
    footer {{ text-align: center; color: #94a3b8; font-size: .8rem; margin-top: 2rem; }}
    @media (max-width: 640px) {{
      .stats-grid {{ grid-template-columns: repeat(2, 1fr); }}
      td.title-cell {{ max-width: 160px; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>Price Report — {html.escape(keyword)}</h1>
      <p>Category: {html.escape(category_label)} &nbsp;·&nbsp; Period: {days} days &nbsp;·&nbsp;
         Generated: {html.escape(generated_at)} &nbsp;·&nbsp; Trend: {trend_html}</p>
    </header>

    <div class="stats-grid">
      <div class="stat">
        <div class="label">Items Tracked</div>
        <div class="value">{summary['item_count']}</div>
      </div>
      <div class="stat">
        <div class="label">Snapshots</div>
        <div class="value">{summary['snapshot_count']}</div>
      </div>
      <div class="stat">
        <div class="label">Avg Price</div>
        <div class="value yen">{avg_str}</div>
      </div>
      <div class="stat">
        <div class="label">Min Price</div>
        <div class="value yen">{_fmt_yen(summary['min_price'])}</div>
      </div>
      <div class="stat">
        <div class="label">Max Price</div>
        <div class="value yen">{_fmt_yen(summary['max_price'])}</div>
      </div>
      <div class="stat">
        <div class="label">Median Price</div>
        <div class="value yen">{median_str}</div>
      </div>
      <div class="stat">
        <div class="label">Avg Bids</div>
        <div class="value">{avg_bids}</div>
      </div>
    </div>

    {chart_section}

    <div class="card">
      <h2>Price History</h2>
      {no_data_msg}
      {'<div style="overflow-x:auto"><table>' if history else ''}
      {'<thead><tr><th>Date</th><th>Title</th><th class="num">Price</th><th class="num">Buy-Now</th><th class="num">Bids</th><th>Condition</th><th>Status</th></tr></thead><tbody>' if history else ''}
      {table_rows}
      {'</tbody></table></div>' if history else ''}
    </div>

    <footer>Yahoo Japan Auction Price Tracker &nbsp;·&nbsp; Data sourced from auctions.yahoo.co.jp</footer>
  </div>
  {chart_script}
</body>
</html>"""


def generate_items_html(
    items: list,
    *,
    title: str,
    category_path: list[str],
    category_id: Optional[str] = None,
    keyword: Optional[str] = None,
    generated_at: Optional[str] = None,
) -> str:
    """Render a one-shot, standalone HTML page listing scraped AuctionItem rows.

    Used by category-shortcut commands (e.g. `kaiju`) where the goal is a
    point-in-time snapshot of items, not a price-history report.
    """
    if generated_at is None:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    breadcrumb = " › ".join(html.escape(p) for p in category_path) if category_path else "—"
    cat_id_label = html.escape(category_id) if category_id else "—"
    kw_label = html.escape(keyword) if keyword else "(all items)"

    prices = [it.current_price for it in items if it.current_price is not None]
    bids = [it.bid_count for it in items if it.bid_count is not None]
    closed_count = sum(1 for it in items if getattr(it, "is_closed", False))

    stat_total = len(items)
    stat_min = _fmt_yen(min(prices)) if prices else "—"
    stat_max = _fmt_yen(max(prices)) if prices else "—"
    stat_avg = _fmt_yen(round(sum(prices) / len(prices))) if prices else "—"
    stat_avg_bids = round(sum(bids) / len(bids), 1) if bids else "—"
    stat_closed = closed_count

    rows_html = ""
    for it in items:
        esc_title = html.escape(it.title or "")
        esc_url = html.escape(it.item_url or "#")
        cur = it.current_price if it.current_price is not None else ""
        buy = it.buynow_price if it.buynow_price is not None else ""
        bids = it.bid_count if it.bid_count is not None else 0
        time_left = it.time_remaining or ""
        cond = it.condition or ""
        is_closed = bool(getattr(it, "is_closed", False))
        status_key = "closed" if is_closed else "active"
        rows_html += f"""
        <tr>
          <td class="title-cell" data-sort="{html.escape(esc_title)}"><a href="{esc_url}" target="_blank" rel="noopener">{esc_title}</a></td>
          <td class="num" data-sort="{cur}">{_fmt_yen(it.current_price)}</td>
          <td class="num" data-sort="{buy}">{_fmt_yen(it.buynow_price)}</td>
          <td class="num" data-sort="{bids}">{bids}</td>
          <td data-sort="{html.escape(time_left)}">{html.escape(time_left or "—")}</td>
          <td data-sort="{cond}">{_condition_badge(it.condition)}</td>
          <td data-sort="{status_key}">{_status_badge(1 if is_closed else 0)}</td>
        </tr>"""

    no_data = "" if items else '<p class="no-data">No items found.</p>'
    table_open = '<div style="overflow-x:auto"><table id="items-table">' if items else ""
    thead = (
        '<thead><tr>'
        '<th class="sortable" data-col="0" data-type="text">Title</th>'
        '<th class="sortable num" data-col="1" data-type="num">Price</th>'
        '<th class="sortable num" data-col="2" data-type="num">Buy-Now</th>'
        '<th class="sortable num" data-col="3" data-type="num">Bids</th>'
        '<th class="sortable" data-col="4" data-type="text">Time Left</th>'
        '<th class="sortable" data-col="5" data-type="text">Condition</th>'
        '<th class="sortable" data-col="6" data-type="text">Status</th>'
        '</tr></thead><tbody>'
    ) if items else ""
    table_close = "</tbody></table></div>" if items else ""

    # Click-to-sort script. Uses each cell's data-sort attribute as the
    # sort key; empty values sort to the end regardless of direction.
    sort_script = """
    <script>
      (function () {
        const table = document.getElementById('items-table');
        if (!table) return;
        const tbody = table.querySelector('tbody');
        const ths = table.querySelectorAll('th.sortable');
        ths.forEach((th) => {
          th.addEventListener('click', () => {
            const col = parseInt(th.dataset.col, 10);
            const isNum = th.dataset.type === 'num';
            const prev = th.dataset.dir;
            const dir = prev === 'asc' ? 'desc' : 'asc';
            ths.forEach((o) => { delete o.dataset.dir; o.classList.remove('sort-asc','sort-desc'); });
            th.dataset.dir = dir;
            th.classList.add('sort-' + dir);
            const rows = Array.from(tbody.querySelectorAll('tr'));
            rows.sort((a, b) => {
              const av = a.children[col].dataset.sort ?? '';
              const bv = b.children[col].dataset.sort ?? '';
              const aEmpty = av === '';
              const bEmpty = bv === '';
              if (aEmpty && bEmpty) return 0;
              if (aEmpty) return 1;          // empty always last
              if (bEmpty) return -1;
              if (isNum) {
                const an = Number(av), bn = Number(bv);
                return dir === 'asc' ? an - bn : bn - an;
              }
              const cmp = String(av).localeCompare(String(bv), 'ja');
              return dir === 'asc' ? cmp : -cmp;
            });
            const frag = document.createDocumentFragment();
            rows.forEach((r) => frag.appendChild(r));
            tbody.appendChild(frag);
          });
        });
      })();
    </script>"""

    sortable_css = """
    th.sortable { cursor: pointer; user-select: none; position: relative; }
    th.sortable:hover { color: #2563eb; }
    th.sortable::after { content: ''; display: inline-block; width: 0; height: 0;
                         margin-left: 6px; vertical-align: middle; opacity: .4;
                         border-left: 4px solid transparent; border-right: 4px solid transparent;
                         border-top: 5px solid #94a3b8; border-bottom: 0; }
    th.sortable.sort-asc::after  { border-top: 0; border-bottom: 5px solid #2563eb; opacity: 1; }
    th.sortable.sort-desc::after { border-top: 5px solid #2563eb; border-bottom: 0; opacity: 1; }
    """

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(title)}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Hiragino Sans",
                   "Yu Gothic", Meiryo, sans-serif;
      background: #f8fafc;
      color: #1e293b;
      padding: 2rem 1rem;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    header {{ margin-bottom: 1.5rem; }}
    header h1 {{ font-size: 1.75rem; font-weight: 700; color: #0f172a; }}
    header .breadcrumb {{ color: #64748b; margin-top: .35rem; font-size: .95rem; }}
    header .meta {{ color: #94a3b8; margin-top: .35rem; font-size: .85rem; }}
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    .stat {{
      background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
      padding: 1rem 1.15rem;
    }}
    .stat .label {{ font-size: .72rem; font-weight: 600; text-transform: uppercase;
                    letter-spacing: .05em; color: #94a3b8; margin-bottom: .25rem; }}
    .stat .value {{ font-size: 1.3rem; font-weight: 700; color: #0f172a; }}
    .stat .value.yen {{ color: #2563eb; }}
    .card {{ background: #fff; border: 1px solid #e2e8f0; border-radius: 12px;
             padding: 1.25rem; }}
    table {{ width: 100%; border-collapse: collapse; font-size: .875rem; }}
    thead tr {{ background: #f1f5f9; }}
    th {{ text-align: left; padding: .65rem .9rem; font-size: .72rem; font-weight: 600;
          text-transform: uppercase; letter-spacing: .05em; color: #64748b;
          white-space: nowrap; }}
    td {{ padding: .55rem .9rem; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: #f8fafc; }}
    td.num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    td.title-cell {{ max-width: 460px; }}
    td.title-cell a {{ color: #2563eb; text-decoration: none; display: block;
                       white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    td.title-cell a:hover {{ text-decoration: underline; }}
    .no-data {{ color: #94a3b8; font-style: italic; padding: 1rem 0; }}
    footer {{ text-align: center; color: #94a3b8; font-size: .8rem; margin-top: 2rem; }}
    @media (max-width: 640px) {{ td.title-cell {{ max-width: 200px; }} }}
    {sortable_css}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>{html.escape(title)}</h1>
      <p class="breadcrumb">{breadcrumb}</p>
      <p class="meta">Category ID: {cat_id_label} &nbsp;·&nbsp; Keyword: {kw_label} &nbsp;·&nbsp;
         Generated: {html.escape(generated_at)}</p>
    </header>

    <div class="stats-grid">
      <div class="stat"><div class="label">Items</div><div class="value">{stat_total}</div></div>
      <div class="stat"><div class="label">Min Price</div><div class="value yen">{stat_min}</div></div>
      <div class="stat"><div class="label">Max Price</div><div class="value yen">{stat_max}</div></div>
      <div class="stat"><div class="label">Avg Price</div><div class="value yen">{stat_avg}</div></div>
      <div class="stat"><div class="label">Avg Bids</div><div class="value">{stat_avg_bids}</div></div>
      <div class="stat"><div class="label">Closed</div><div class="value">{stat_closed}</div></div>
    </div>

    <div class="card">
      {no_data}
      {table_open}
      {thead}
      {rows_html}
      {table_close}
    </div>

    <footer>Yahoo Japan Auction Price Tracker &nbsp;·&nbsp; Data sourced from auctions.yahoo.co.jp</footer>
  </div>
  {sort_script}
</body>
</html>"""
