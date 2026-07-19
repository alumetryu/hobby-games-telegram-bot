import json
import logging
import time
from pathlib import Path

from paths import DATA_DIR

logger = logging.getLogger(__name__)

DATA_FILE = DATA_DIR / "users_data.json"


def _load_data() -> dict:
    if not DATA_FILE.exists():
        return {"users": {}}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"users": {}}


def _save_data(data: dict) -> None:
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error(f"Ошибка записи users_data.json: {exc}")


def record_user(user_id: int, username: str = "", first_name: str = "", last_name: str = "") -> None:
    data = _load_data()
    uid = str(user_id)
    now = int(time.time())

    if uid in data["users"]:
        data["users"][uid]["last_seen"] = now
        data["users"][uid]["interactions"] = data["users"][uid].get("interactions", 0) + 1
    else:
        data["users"][uid] = {
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "first_seen": now,
            "last_seen": now,
            "interactions": 1,
        }

    _save_data(data)


def get_stats() -> dict:
    data = _load_data()
    users = data.get("users", {})
    total = len(users)
    today_start = int(time.time()) - 86400

    active_today = 0
    total_interactions = 0
    for u in users.values():
        total_interactions += u.get("interactions", 0)
        if u.get("last_seen", 0) >= today_start:
            active_today += 1

    return {
        "total_users": total,
        "active_today": active_today,
        "total_interactions": total_interactions,
    }
