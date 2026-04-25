# User Manual — Yahoo Japan Auction Price Tracker

## Table of Contents

1. [Overview](#1-overview)
2. [Requirements](#2-requirements)
3. [Installation](#3-installation)
4. [Project Structure](#4-project-structure)
5. [Command Reference](#5-command-reference)
   - [search](#51-search)
   - [track](#52-track)
   - [report](#53-report)
   - [schedule](#54-schedule)
   - [list](#55-list)
   - [delete](#56-delete)
   - [list-categories](#57-list-categories)
   - [japan-time](#58-japan-time)
6. [Category Filtering](#6-category-filtering)
7. [Database](#7-database)
8. [Japan Standard Time](#8-japan-standard-time)
9. [Scheduling Daily Updates](#9-scheduling-daily-updates)
10. [Exporting Data](#10-exporting-data)
11. [Troubleshooting](#11-troubleshooting)
12. [Important Notes](#12-important-notes)

---

## 1. Overview

**Yahoo Japan Auction Price Tracker** is a command-line Python tool that:

- Scrapes active and sold auction listings from [auctions.yahoo.co.jp](https://auctions.yahoo.co.jp)
- Records daily price snapshots into a local SQLite database
- Tracks price history and computes trend summaries (rising / falling / stable)
- Filters results by product category or keyword
- Fetches the current **Japan Standard Time (JST)** from `worldtimeapi.org` to ensure that daily snapshots are stamped with the correct date in Japan

---

## 2. Requirements

| Requirement | Version |
|-------------|---------|
| Python      | 3.9 or higher (3.11 recommended) |
| pip         | any recent version |
| Internet    | required for scraping and JST fetch |

All Python dependencies are listed in `requirements.txt`.

---

## 3. Installation

```bash
# 1. Clone or download the repository
git clone <repo-url>
cd trace_price_yahoo_jp

# 2. (Optional but recommended) Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt
```

Verify the installation:

```bash
python main.py --help
```

You should see the list of available commands.

---

## 4. Project Structure

```
trace_price_yahoo_jp/
├── main.py                          # CLI entry point
├── requirements.txt                 # Python dependencies
├── auction_tracker.db               # SQLite database (created on first run)
├── USER_MANUAL.md                   # This file
├── README.md                        # Quick reference
└── yahoo_auction_tracker/
    ├── __init__.py
    ├── config.py                    # Category IDs, defaults, URLs
    ├── database.py                  # SQLite schema and query functions
    ├── scraper.py                   # HTTP requests and HTML parsing
    └── tracker.py                   # Orchestration and APScheduler logic
```

---

## 5. Command Reference

All commands follow this pattern:

```
python main.py [GLOBAL OPTIONS] COMMAND [COMMAND OPTIONS]
```

**Global options** (apply to every command):

| Option | Description |
|--------|-------------|
| `--db PATH` | Path to the SQLite database file (default: `auction_tracker.db` in the current directory) |
| `-v / --verbose` | Enable debug-level logging, including full stack traces |
| `--help` | Show help text |

---

### 5.1 `search`

Search Yahoo Japan Auctions and display results **without** saving anything to the database. Use this to preview results before committing to tracking.

```
python main.py search --keyword KEYWORD [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-k / --keyword` | *(required)* | Search keyword (Japanese or English) |
| `-c / --category` | none | Category alias or numeric ID (see §6) |
| `--closed` | false | Search sold/closed auctions instead of active ones |
| `--pages` | 2 | Number of result pages to fetch (1–5, 50 items per page) |

**Examples:**

```bash
# Search for active Nintendo Switch listings
python main.py search --keyword "Nintendo Switch"

# Search within the games category
python main.py search --keyword "Nintendo Switch" --category games

# Search using a sub-category alias
python main.py search --keyword "Nintendo Switch" --category nintendo_switch

# Search using a raw numeric category ID
python main.py search --keyword "カメラ" --category 2084020887

# Search closed (sold) auctions, 3 pages
python main.py search --keyword "PS5" --category ps5 --closed --pages 3
```

**Output columns:**

| Column | Description |
|--------|-------------|
| Title | Auction listing title (truncated to 45 characters) |
| Price | Current bid price |
| Buy-Now | Fixed buy-it-now price (空 if none) |
| Bids | Number of bids placed |
| Time Remaining | Countdown string from Yahoo |
| Condition | `new` / `used` / `-` |

---

### 5.2 `track`

Run a **one-shot scrape** and save the results to the database. This is the command to run manually or via an external scheduler (e.g. cron).

```
python main.py track --keyword KEYWORD [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-k / --keyword` | *(required)* | Search keyword |
| `-c / --category` | none | Category alias or numeric ID |
| `--no-closed` | false | Skip closed auction search (faster, active only) |

**Examples:**

```bash
# Track Nintendo Switch prices (active + closed auctions)
python main.py track --keyword "Nintendo Switch" --category nintendo_switch

# Track only active listings
python main.py track --keyword "Rolex" --category watches --no-closed

# Use a different database file
python main.py --db ~/my_prices.db track --keyword "MacBook Pro"
```

After running, a summary table is printed:

```
╭────────────────────────┬──────────────╮
│ Snapshot date          │ 2024-05-01   │
│ Keyword                │ Nintendo ... │
│ Active items fetched   │ 143          │
│ Closed items fetched   │ 98           │
│ New items saved        │ 241          │
│ Items updated          │ 0            │
╰────────────────────────┴──────────────╯
```

Running `track` on the same day again updates prices rather than creating duplicates.

---

### 5.3 `report`

Display price history and statistical summary for a tracked keyword.

```
python main.py report --keyword KEYWORD [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-k / --keyword` | *(required)* | Keyword to report on |
| `-c / --category` | none | Category alias or numeric ID |
| `--days` | 30 | Number of past days to include |
| `--format` | `table` | Output format: `table` or `csv` |

**Examples:**

```bash
# Show 30-day report
python main.py report --keyword "Nintendo Switch"

# Show 7-day report for a specific category
python main.py report --keyword "Nintendo Switch" --category nintendo_switch --days 7

# Export as CSV
python main.py report --keyword "Nintendo Switch" --format csv > switch_prices.csv
```

**Summary block** includes:

| Field | Description |
|-------|-------------|
| Period | Number of days covered |
| Items tracked | Distinct auction items seen |
| Snapshots | Total data points |
| Avg / Min / Max / Median price | In Japanese Yen |
| Avg bids | Average number of bids |
| Trend | `up` / `down` / `stable` / `insufficient_data` |

The **trend** is calculated by comparing the average price in the first half of the period against the second half. A change of more than ±3% is classified as `up` or `down`.

---

### 5.4 `schedule`

Schedule **automatic daily tracking** at a fixed time in JST. This command blocks the terminal until you press `Ctrl+C`.

```
python main.py schedule --keyword KEYWORD [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-k / --keyword` | *(required)* | Keyword to track |
| `-c / --category` | none | Category alias or numeric ID |
| `--time` | `09:00` | Daily run time in `HH:MM` format (Japan Standard Time) |
| `--run-now` | false | Run one immediate update before entering the daily loop |
| `--no-closed` | false | Skip closed auction search each run |

**Examples:**

```bash
# Schedule at 09:00 JST, run immediately first
python main.py schedule --keyword "Nintendo Switch" --category games --run-now

# Schedule at a custom time
python main.py schedule --keyword "PS5" --time 22:30

# Run in the background using nohup (Linux/macOS)
nohup python main.py schedule --keyword "Nintendo Switch" --time 09:00 > tracker.log 2>&1 &
```

The scheduler respects Japan Standard Time regardless of the system's local timezone, because the time is sourced from `worldtimeapi.org` (see §8).

---

### 5.5 `list`

List all keyword/category configurations that have been tracked.

```
python main.py list
```

**Output columns:** ID, Keyword, Category, Last Run (UTC), Items, Status.

Use the **ID** value with the `delete` command to remove a search.

---

### 5.6 `delete`

Remove a tracked search and **all its associated price history** from the database.

```
python main.py delete SEARCH_ID
```

This action is permanent. Check the ID first with `python main.py list`.

**Example:**

```bash
python main.py delete 3
```

---

### 5.7 `list-categories`

Print all supported category aliases and their numeric Yahoo Japan category IDs.

```
python main.py list-categories
```

---

### 5.8 `japan-time`

Fetch and display the current Japan Standard Time from `worldtimeapi.org`. Falls back to the system clock if the request fails.

```
python main.py japan-time
```

---

## 6. Category Filtering

You can filter search results by product category using:

- A **category alias** (short English name)
- A **sub-category alias**
- A **raw numeric category ID** from Yahoo Japan

### Supported Aliases

| Alias | Sub-alias | Japanese Name | Category ID |
|-------|-----------|---------------|-------------|
| `electronics` | | パソコン・周辺機器 | 2084082530 |
| | `laptops` | ノートPC | 2084084663 |
| | `desktop` | デスクトップPC | 2084084658 |
| | `monitors` | ディスプレイ | 2084085217 |
| `games` | | テレビゲーム | 2084017887 |
| | `nintendo_switch` | Nintendo Switch | 2084030018 |
| | `ps5` | PlayStation 5 | 2084048606 |
| | `ps4` | PlayStation 4 | 2084036323 |
| `cameras` | | カメラ・光学機器 | 2084020887 |
| | `dslr` | デジタル一眼レフカメラ | 2084023038 |
| | `mirrorless` | ミラーレス一眼 | 2084143869 |
| `watches` | | 時計 | 26318 |
| `clothing` | | ファッション | 2084228509 |
| `toys` | | おもちゃ・ホビー・グッズ | 2084203698 |
| `sports` | | スポーツ・レジャー | 2084199712 |
| `cars` | | 自動車・バイク | 2084134255 |

### Using a Custom Category ID

Browse Yahoo Japan Auctions in your browser, select a category, and copy the `auccat` value from the URL:

```
https://auctions.yahoo.co.jp/search/search?p=keyword&auccat=2084030018
                                                              ^^^^^^^^^^
                                                              category ID
```

Then pass it directly:

```bash
python main.py search --keyword "キーワード" --category 2084030018
```

---

## 7. Database

All data is stored in a single **SQLite** file (`auction_tracker.db` by default).

### Tables

| Table | Purpose |
|-------|---------|
| `searches` | One row per keyword + category combination being tracked |
| `items` | One row per unique auction item ever seen (identified by Yahoo's item ID) |
| `price_history` | One price snapshot per item per day |

### Changing the Database Path

```bash
python main.py --db /path/to/custom.db track --keyword "MacBook"
```

### Backing Up

Because the data is a single file, backup is straightforward:

```bash
cp auction_tracker.db auction_tracker_backup_$(date +%Y%m%d).db
```

### Inspecting Data Directly

```bash
sqlite3 auction_tracker.db

# Examples
SELECT * FROM searches;
SELECT snapshot_date, AVG(current_price) FROM price_history GROUP BY snapshot_date;
.quit
```

---

## 8. Japan Standard Time

The snapshot date attached to each price record must reflect the **date in Japan**, not the date on the machine running the script. This matters if you run the tracker outside of Japan (e.g. UTC+0 or UTC-5 time zones) — midnight in Japan is mid-afternoon or earlier the previous day in those zones.

### How It Works

1. On every `track` or `schedule` run, `get_japan_time()` in `tracker.py` sends a GET request to:
   ```
   https://worldtimeapi.org/api/timezone/Asia/Tokyo
   ```
2. The response contains a `datetime` field with timezone offset `+09:00`.
3. This is parsed and used as the `snapshot_date` (YYYY-MM-DD in JST).
4. If `worldtimeapi.org` is unreachable (network error, HTTP error), the function automatically falls back to the **system clock converted to JST** using Python's `zoneinfo.ZoneInfo("Asia/Tokyo")`.

You can verify the current JST at any time:

```bash
python main.py japan-time
```

---

## 9. Scheduling Daily Updates

### Option A — Built-in Scheduler (foreground)

```bash
python main.py schedule --keyword "Nintendo Switch" --category games \
    --time 09:00 --run-now
```

This blocks the terminal. Use `nohup` or `screen`/`tmux` to keep it running after you close the terminal.

### Option B — System Cron (Linux/macOS)

Add a cron job to run `track` once per day:

```bash
crontab -e
```

Add the following line to run at 09:00 JST (adjust for your server's timezone):

```
0 9 * * * cd /path/to/trace_price_yahoo_jp && python main.py track --keyword "Nintendo Switch" --category games >> tracker.log 2>&1
```

### Option C — Task Scheduler (Windows)

1. Open **Task Scheduler** → Create Basic Task
2. Set the trigger to **Daily** at your desired time
3. Set the action to run:
   - Program: `python`
   - Arguments: `main.py track --keyword "Nintendo Switch" --category games`
   - Start in: `C:\path\to\trace_price_yahoo_jp`

---

## 10. Exporting Data

### CSV Export via CLI

```bash
python main.py report --keyword "Nintendo Switch" --format csv > nintendo_switch.csv
```

The CSV columns are:

```
date, item_id, title, current_price, buynow_price, bid_count, condition, is_closed, item_url
```

### Direct SQLite Export

```bash
sqlite3 -csv -header auction_tracker.db \
  "SELECT * FROM price_history ORDER BY snapshot_date DESC;" \
  > full_export.csv
```

---

## 11. Troubleshooting

### No results returned

- Yahoo Japan may have temporarily blocked the request. Wait a few minutes and try again.
- Try adding `--verbose` to see the full request/response log.
- Verify the category ID is correct with `python main.py list-categories`.

### `RateLimitError: CAPTCHA/block page detected`

Yahoo Japan detected automated access. Recommended steps:

1. Wait 10–30 minutes before retrying.
2. Reduce the number of pages: `--pages 1`.
3. Run during off-peak hours (late night JST).

### `Could not fetch Japan time from web`

This warning appears when `worldtimeapi.org` is unreachable. The system clock is used as a fallback. If your server is in the correct timezone this has no effect; if it is not, set the system timezone to `Asia/Tokyo` or ensure network access to `worldtimeapi.org`.

### Database is locked

Only one process should write to the database at a time. If you accidentally started two `schedule` commands for the same database, stop one of them. The database uses WAL mode, which allows concurrent reads.

### `ModuleNotFoundError`

Run `pip install -r requirements.txt` again. If using a virtual environment, make sure it is activated.

---

## 12. Important Notes

- This tool is intended for **personal research and price monitoring** only.
- Please respect Yahoo Japan's [Terms of Service](https://auctions.yahoo.co.jp/legal/terms).
- Requests are throttled to approximately **1.5 seconds between pages** to avoid overloading Yahoo's servers.
- Avoid running more than one concurrent tracking process against the same keyword.
- Yahoo Japan may change their HTML structure at any time, which could break parsing. A warning will be printed if more than 50% of items on a page cannot be parsed.
