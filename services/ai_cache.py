"""
Простой кэш рекомендаций ИИ на основе SQLite.

Зачем:
  Если пользователь задаёт похожий запрос (или тот же самый),
  не тратим токены Gemini — берём прошлый ответ.

Что кэшируется:
  - Нормализованный запрос (ключ)
  - Полный текст ответа Gemini
  - Список названий игр
  - Дата создания (TTL = 7 дней)

SQLite выбран намеренно — никаких дополнительных зависимостей,
работает из коробки, достаточно для 1 магазина.
"""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from paths import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH = str(DATA_DIR / "ai_cache.db")
CACHE_TTL_DAYS = 7


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            key         TEXT PRIMARY KEY,
            query       TEXT NOT NULL,
            ai_response TEXT NOT NULL,
            game_names  TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def _normalize(query: str) -> str:
    """Нормализует запрос: нижний регистр, слова по алфавиту."""
    return " ".join(sorted(query.lower().split()))


def _key(query: str) -> str:
    return hashlib.md5(_normalize(query).encode()).hexdigest()


def get_from_cache(query: str) -> dict | None:
    """
    Возвращает {"ai_response": str, "game_names": list[str]} или None.
    """
    try:
        conn = _get_conn()
        row = conn.execute(
            "SELECT ai_response, game_names, created_at FROM cache WHERE key = ?",
            (_key(query),),
        ).fetchone()
        conn.close()

        if not row:
            return None

        ai_response, game_names_json, created_at_str = row
        created_at = datetime.fromisoformat(created_at_str)
        if datetime.now() - created_at > timedelta(days=CACHE_TTL_DAYS):
            logger.info(f"Кэш устарел для запроса «{query}»")
            return None

        logger.info(f"Кэш hit для «{query}»")
        return {
            "ai_response": ai_response,
            "game_names": json.loads(game_names_json),
        }
    except Exception as exc:
        logger.error(f"Ошибка чтения кэша: {exc}")
        return None


def save_to_cache(query: str, ai_response: str, game_names: list[str]) -> None:
    """Сохраняет ответ ИИ в кэш."""
    try:
        conn = _get_conn()
        conn.execute(
            """
            INSERT OR REPLACE INTO cache (key, query, ai_response, game_names, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                _key(query),
                query,
                ai_response,
                json.dumps(game_names, ensure_ascii=False),
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        logger.info(f"Кэш сохранён для «{query}»: {len(game_names)} игр")
    except Exception as exc:
        logger.error(f"Ошибка записи кэша: {exc}")
