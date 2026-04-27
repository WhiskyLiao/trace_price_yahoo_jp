# Yahoo Japan Auction Price Tracker

Daily price tracking for Yahoo Japan Auctions (auctions.yahoo.co.jp).  
Filters by category, stores history in a local SQLite database, and uses
**worldtimeapi.org** to fetch accurate Japan Standard Time for daily snapshots.

---

## Installation

### Windows

1. Download and install Python 3.11+ from [python.org](https://www.python.org/downloads/)  
   ⚠️ During installation, check **"Add Python to PATH"**

2. Open **Command Prompt** (`Win + R` → type `cmd` → Enter)

3. Navigate to the project folder:
   ```cmd
   cd C:\path\to\trace_price_yahoo_jp
   ```

4. Create and activate a virtual environment:
   ```cmd
   python -m venv .venv
   .venv\Scripts\activate
   ```
   The prompt will change to `(.venv)` confirming it is active.

5. Install dependencies:
   ```cmd
   pip install -r requirements.txt
   ```

### macOS / Linux

```bash
cd trace_price_yahoo_jp
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Quick Start

Keyword can be typed interactively — just run a command without `--keyword` and you will be prompted:

```
Search keyword: カメラ
```

Or pass it directly on the command line:

**Windows (Command Prompt):**
```cmd
:: Search without saving (preview)
python main.py search --category cameras

:: Track once (scrape + save to DB)
python main.py track --category cameras

:: Generate HTML report
python main.py report --format html

:: Schedule daily tracking at 09:00 JST
python main.py schedule --time 09:00 --run-now

:: Export history as CSV
python main.py report --format csv --output prices.csv

:: List all tracked searches
python main.py list

:: Show current Japan time
python main.py japan-time
```

**macOS / Linux:**
```bash
# Search without saving (preview)
python main.py search --category cameras

# Track once (scrape + save to DB)
python main.py track --category cameras

# Generate HTML report
python main.py report --format html

# Schedule daily tracking at 09:00 JST
python main.py schedule --time 09:00 --run-now

# Export history as CSV
python main.py report --format csv > prices.csv

# List all tracked searches
python main.py list

# Show current Japan time
python main.py japan-time

# Browse categories interactively
python main.py browse
```

---

## Browsing Categories

Yahoo Japan Auctions has hundreds of nested subcategories. Use the `browse` command to drill down interactively and find the exact category ID you want.

```cmd
python main.py browse
```

Example session:

```
Fetching categories from Yahoo Japan Auctions...

==================================================
  Yahoo Japan Auction Categories
==================================================
    1.  アンティーク、コレクション  [2084005020]
    2.  カメラ、光学機器  [2084020887]
    3.  テレビゲーム  [2084017887]
    4.  時計  [26318]
    ...

    0.  Cancel

Select: 2

Fetching subcategories for "カメラ、光学機器"...

==================================================
  カメラ、光学機器
==================================================
    1.  フィルムカメラ  [2084021077]
    2.  デジタルカメラ  [2084021107]
    3.  ミラーレスカメラ  [2084143869]
    4.  デジタル一眼レフ  [2084023038]
    ...

    0.  ✓ Use "カメラ、光学機器" (ID: 2084020887)
    B.  ← Go back

Select: 3

Selected: カメラ、光学機器 > ミラーレスカメラ
Category ID: 2084143869

Tip: use with other commands:  --category 2084143869
```

Then use the returned ID:

```cmd
python main.py search --category 2084143869
python main.py track --category 2084143869
python main.py report --category 2084143869 --format html
```

The category tree is **cached locally** (`categories_cache.json`) for 7 days, so subsequent `browse` runs are instant.

### Automatic ID refresh

Yahoo Japan rotates category IDs occasionally, so the static IDs shipped in
`config.py` will eventually go stale. To handle this transparently:

- Every `search`, `track`, and scheduled run looks up the alias in
  `categories_cache.json` and uses the **current live ID** for that
  category's Japanese name.
- The cache auto-refreshes from Yahoo when missing or older than 7 days —
  no need to run `browse` first.
- If the live ID differs from the static one, you'll see a one-line note:
  `Note: category 'cameras' ID refreshed 2084020887 → <new>`.
- Numeric `--category 1234567` IDs pass through untouched.
- If the network is unreachable, the static ID is used and the search still
  proceeds; a final 404 fallback in the scraper retries without the filter.

For long-running `schedule` jobs, the refresh runs **before each daily
run**, so a multi-week schedule self-heals when Yahoo changes an ID.

---

## Scheduling Daily Updates

### Windows — Task Scheduler

1. Open **Task Scheduler** (search in Start Menu)
2. Click **Create Basic Task** on the right panel
3. Fill in the settings:

| Field | Value |
|-------|-------|
| Name | Auction Price Tracker |
| Trigger | Daily |
| Time | 09:00 (note: JST = UTC+9, adjust if your PC clock is not JST) |
| Action | Start a program |
| Program | `C:\path\to\trace_price_yahoo_jp\.venv\Scripts\python.exe` (or just `python`) |
| Arguments | `main.py track --category cameras` |
| Start in | `C:\path\to\trace_price_yahoo_jp` |

### macOS / Linux — cron

```bash
crontab -e
# Add (runs at 09:00 JST — adjust hour for your server timezone):
0 9 * * * cd /path/to/trace_price_yahoo_jp && python main.py track >> tracker.log 2>&1
```

---

## Category Aliases

```bash
python main.py list-categories
```

| Alias            | Japanese Name              |
|------------------|----------------------------|
| electronics      | パソコン・周辺機器          |
| laptops          | ノートPC                   |
| desktop          | デスクトップPC              |
| games            | テレビゲーム                |
| nintendo_switch  | 任天堂スイッチ              |
| ps5              | プレイステーション5          |
| ps4              | プレイステーション4          |
| cameras          | カメラ・光学機器            |
| dslr             | デジタル一眼レフカメラ       |
| mirrorless       | ミラーレス一眼              |
| watches          | 時計                       |
| clothing         | ファッション                |
| toys             | おもちゃ・ホビー・グッズ     |
| sports           | スポーツ・レジャー           |
| cars             | 自動車・バイク              |

You can also pass a raw numeric category ID: `--category 2084030018`

---

## CLI Reference

| Command           | Description                                              |
|-------------------|----------------------------------------------------------|
| `browse`          | Interactively browse live Yahoo Japan category tree      |
| `search`          | Search and display results (no DB write)                 |
| `track`           | One-shot scrape and save to database                     |
| `report`          | Show price history and stats                             |
| `schedule`        | Schedule daily tracking (blocks, Ctrl+C to stop)         |
| `list`            | List all tracked searches                                |
| `delete <ID>`     | Remove a search and all its history                      |
| `list-categories` | Show built-in category aliases and IDs                   |
| `japan-time`      | Display current JST from worldtimeapi.org                |
| `kaiju`           | Shortcut: scrape Godzilla/Kaiju figures → standalone HTML |

`--keyword` / `-k` can be omitted from any command — you will be prompted to enter it interactively.

---

## HTML Report

```cmd
python main.py report --format html
```

Generates a standalone `.html` file with a price chart and history table.  
Open it in any browser — no server or internet required to view the table.

---

## Quick Category: ゴジラ・怪獣

A built-in shortcut that walks the live Yahoo Japan category tree, starting
from depth 0:

```
オークショントップ → おもちゃ、ゲーム → フィギュア → 特撮 → ゴジラ、怪獣
```

scrapes the leaf, and writes a self-contained HTML file — no DB, no
scheduling, one command:

```cmd
python main.py kaiju
```

Output goes to `kaiju_report.html` in the current directory by default.
Useful flags:

| Flag                | Default              | Purpose                                   |
|---------------------|----------------------|-------------------------------------------|
| `-k` / `--keyword`  | (empty — all items)  | Narrow within the category                |
| `--pages`           | `2`                  | Pages to fetch (1–5)                      |
| `--include-closed`  | off                  | Also include sold auctions                |
| `-o` / `--output`   | `kaiju_report.html`  | Output file path                          |

Examples:

```cmd
:: All items, 2 pages
python main.py kaiju

:: Only "ソフビ" within the category, 5 pages, save elsewhere
python main.py kaiju -k ソフビ --pages 5 -o sofubi.html

:: Include sold listings
python main.py kaiju --include-closed
```

The path is resolved against the live tree on every run, so no static IDs
to maintain — if Yahoo rotates the leaf, the next run picks up the new ID
automatically.

---

## Database

Data is stored in `auction_tracker.db` (SQLite) in the current directory.  
Use `--db C:\path\to\custom.db` to specify a different path.

`report` opens the database read-only and never creates it; if the file
doesn't exist yet, run `track` first.

**Backup (Windows):**
```cmd
copy auction_tracker.db auction_tracker_backup.db
```

**Backup (macOS / Linux):**
```bash
cp auction_tracker.db auction_tracker_backup_$(date +%Y%m%d).db
```

---

## Generated Files

The tool creates the following files in the working directory. All are
covered by `.gitignore`:

| File                       | Created by               | Purpose                                |
|----------------------------|--------------------------|----------------------------------------|
| `auction_tracker.db`       | `track`, `list`, `delete`| SQLite price history database          |
| `categories_cache.json`    | `browse`                 | Local category tree cache (7-day TTL)  |
| `<keyword>_report.html`    | `report --format html`   | Standalone HTML report                 |
| `tracker.log`              | cron example in this README | Schedule run output                 |

---

## Windows Troubleshooting

| Problem | Solution |
|---------|----------|
| `python` not found | Reinstall Python and check "Add Python to PATH" |
| Chinese/Japanese characters show as `?` | Run `chcp 65001` in Command Prompt before starting |
| `pip install` fails with permission error | Run Command Prompt as Administrator, or use `pip install --user -r requirements.txt` |
| Firewall blocks requests | Allow Python through Windows Defender Firewall |

---

## Notes

- Requests are throttled to ~1.5 s between pages.
- For personal research use only; please respect Yahoo Japan's Terms of Service.
