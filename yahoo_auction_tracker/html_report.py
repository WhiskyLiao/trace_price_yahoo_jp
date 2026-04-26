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
