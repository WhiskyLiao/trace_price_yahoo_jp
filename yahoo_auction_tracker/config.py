from __future__ import annotations

BASE_SEARCH_URL = "https://auctions.yahoo.co.jp/search/search"
CLOSED_SEARCH_URL = "https://auctions.yahoo.co.jp/closedsearch/closedsearch"

# worldtimeapi.org endpoint for accurate JST
WORLD_TIME_API_URL = "https://worldtimeapi.org/api/timezone/Asia/Tokyo"

CATEGORIES: dict[str, dict] = {
    "electronics": {
        "name_ja": "パソコン・周辺機器",
        "id": "2084082530",
        "subcategories": {
            "laptops":  {"name_ja": "ノートPC",          "id": "2084084663"},
            "desktop":  {"name_ja": "デスクトップPC",     "id": "2084084658"},
            "monitors": {"name_ja": "ディスプレイ",       "id": "2084085217"},
        },
    },
    "games": {
        "name_ja": "テレビゲーム",
        "id": "2084017887",
        "subcategories": {
            "nintendo_switch": {"name_ja": "任天堂スイッチ",       "id": "2084030018"},
            "ps5":             {"name_ja": "プレイステーション5",   "id": "2084048606"},
            "ps4":             {"name_ja": "プレイステーション4",   "id": "2084036323"},
        },
    },
    "cameras": {
        "name_ja": "カメラ・光学機器",
        "id": "2084020887",
        "subcategories": {
            "dslr":       {"name_ja": "デジタル一眼レフカメラ", "id": "2084023038"},
            "mirrorless": {"name_ja": "ミラーレス一眼",        "id": "2084143869"},
        },
    },
    "watches": {
        "name_ja": "時計",
        "id": "26318",
        "subcategories": {},
    },
    "clothing": {
        "name_ja": "ファッション",
        "id": "2084228509",
        "subcategories": {},
    },
    "toys": {
        "name_ja": "おもちゃ・ホビー・グッズ",
        "id": "2084203698",
        "subcategories": {},
    },
    "sports": {
        "name_ja": "スポーツ・レジャー",
        "id": "2084199712",
        "subcategories": {},
    },
    "cars": {
        "name_ja": "自動車・バイク",
        "id": "2084134255",
        "subcategories": {},
    },
}

# Flat lookup: category_id -> display name (populated below)
CATEGORY_ID_TO_NAME: dict[str, str] = {}

def _build_id_map() -> None:
    for alias, cat in CATEGORIES.items():
        CATEGORY_ID_TO_NAME[cat["id"]] = f"{alias} ({cat['name_ja']})"
        for sub_alias, sub in cat.get("subcategories", {}).items():
            CATEGORY_ID_TO_NAME[sub["id"]] = f"{alias}/{sub_alias} ({sub['name_ja']})"

_build_id_map()


def resolve_category(name_or_id: str | None) -> str | None:
    """Return numeric category ID string given an alias or raw ID, or None."""
    if not name_or_id:
        return None
    # Already a numeric ID
    if name_or_id.isdigit():
        return name_or_id
    # Top-level alias
    if name_or_id in CATEGORIES:
        return CATEGORIES[name_or_id]["id"]
    # Sub-category alias (e.g. "nintendo_switch" or "games/nintendo_switch")
    parts = name_or_id.split("/")
    if len(parts) == 2:
        parent, child = parts
        if parent in CATEGORIES and child in CATEGORIES[parent].get("subcategories", {}):
            return CATEGORIES[parent]["subcategories"][child]["id"]
    for cat in CATEGORIES.values():
        subs = cat.get("subcategories", {})
        if name_or_id in subs:
            return subs[name_or_id]["id"]
    return None


DEFAULT_SETTINGS: dict = {
    "request_delay_seconds": 1.5,
    "items_per_page": 50,
    "max_pages": 5,
    "db_path": "auction_tracker.db",
    "schedule_timezone": "Asia/Tokyo",
    "log_level": "INFO",
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}
