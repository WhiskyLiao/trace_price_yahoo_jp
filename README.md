# Yahoo Japan Auction Price Tracker

Daily price tracking for Yahoo Japan Auctions (auctions.yahoo.co.jp).  
Filters by category, stores history in a local SQLite database, and uses
**worldtimeapi.org** to fetch accurate Japan Standard Time for daily snapshots.

## Installation

```bash
pip install -r requirements.txt
```

## Quick Start

```bash
# Search without saving (preview)
python main.py search --keyword "Nintendo Switch" --category nintendo_switch

# Track once (scrape + save to DB)
python main.py track --keyword "Nintendo Switch" --category games

# View price history and trend
python main.py report --keyword "Nintendo Switch" --days 30

# Schedule daily tracking at 09:00 JST (runs once immediately then daily)
python main.py schedule --keyword "Nintendo Switch" --category games --time 09:00 --run-now

# Export history as CSV
python main.py report --keyword "Nintendo Switch" --format csv > prices.csv

# List all tracked searches
python main.py list

# Show current Japan time (fetched from worldtimeapi.org)
python main.py japan-time
```

## Category Aliases

```bash
python main.py list-categories
```

| Alias            | Japanese Name              |
|------------------|----------------------------|
| electronics      | パソコン・周辺機器           |
| games            | テレビゲーム                |
| nintendo_switch  | Nintendo Switch            |
| ps5              | PlayStation 5              |
| ps4              | PlayStation 4              |
| cameras          | カメラ・光学機器            |
| watches          | 時計                       |
| clothing         | ファッション                |
| toys             | おもちゃ・ホビー・グッズ    |
| sports           | スポーツ・レジャー          |
| cars             | 自動車・バイク              |

You can also pass a raw numeric category ID: `--category 2084030018`

## CLI Reference

| Command           | Description                                      |
|-------------------|--------------------------------------------------|
| `search`          | Search and display results (no DB write)         |
| `track`           | One-shot scrape and save to database             |
| `report`          | Show price history and stats                     |
| `schedule`        | Schedule daily tracking (blocks, Ctrl+C to stop) |
| `list`            | List all tracked searches                        |
| `delete <ID>`     | Remove a search and all its history              |
| `list-categories` | Show category aliases                            |
| `japan-time`      | Display current JST from worldtimeapi.org        |

## Database

Data is stored in `auction_tracker.db` (SQLite) in the current directory.  
Use `--db /path/to/custom.db` to specify a different path.

## Notes

- Requests are throttled to ~1.5 s between pages.
- For personal research use only; please respect Yahoo Japan's Terms of Service.
