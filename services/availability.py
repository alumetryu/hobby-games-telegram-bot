"""
Кэш наличия игр на CloudShop.

Хранит результаты проверки в JSON-файле с TTL = 24 часа.
При показе карточки игры — берёт данные из кэша.
Если кэш протух — делает запрос к сайту и обновляет запись.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from catalog_data import CATALOG
from models import StockItem
from paths import DATA_DIR
from services.cloudshop import search_exact_in_cloudshop

logger = logging.getLogger(__name__)

CACHE_FILE = DATA_DIR / "availability_cache.json"
TTL_SECONDS = 24 * 60 * 60  # 24 часа

# Lock для предотвращения гонки между refresh_all и get_availability
_lock = asyncio.Lock()


def _load_cache() -> dict:
    """Загружает кэш из файла."""
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning(f"Ошибка чтения кэша: {exc}")
        return {}


def _save_cache(cache: dict) -> None:
    """Сохраняет кэш в файл."""
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error(f"Ошибка записи кэша: {exc}")


async def get_availability(game_name: str) -> StockItem | None:
    """
    Получает данные о наличии игры (точное совпадение названия).
    Сначала проверяет кэш, если протух — делает запрос к сайту.

    Возвращает:
      - StockItem с qty > 0 если есть в наличии
      - StockItem с qty = 0 если найдено на сайте, но нет в наличии
      - None если не найдено на сайте или ошибка
    """
    cache = _load_cache()
    now = time.time()

    if game_name in cache:
        entry = cache[game_name]
        if now - entry.get("updated_at", 0) < TTL_SECONDS:
            qty = entry.get("qty", 0)
            price = entry.get("price", "")
            return StockItem(name=game_name, qty=qty, price=price)

    logger.debug(f"Кэш MISS: {game_name}, запрашиваю сайт...")
    try:
        item = await search_exact_in_cloudshop(game_name)
        if item:
            async with _lock:
                cache = _load_cache()
                cache[game_name] = {
                    "qty": item.qty,
                    "price": item.price,
                    "updated_at": now,
                }
                _save_cache(cache)
            return item
        else:
            async with _lock:
                cache = _load_cache()
                cache[game_name] = {
                    "qty": 0,
                    "price": "",
                    "updated_at": now,
                }
                _save_cache(cache)
            return None
    except Exception as exc:
        logger.error(f"Ошибка проверки наличия '{game_name}': {exc}")
        return None


async def refresh_all() -> None:
    """
    Принудительно обновляет кэш для всех игр из каталога.
    Загружает каталог ОДИН раз, фильтрует на Python.
    """
    from services.cloudshop import search_multiple

    all_games = []
    for cat in CATALOG.values():
        for game in cat.get("games", []):
            all_games.append(game["name"])

    logger.info(f"Обновление наличия для {len(all_games)} игр...")

    try:
        results = await search_multiple(all_games)
    except Exception as exc:
        logger.error(f"Ошибка массового обновления: {exc}")
        return

    cache = {}
    now = time.time()
    updated = 0

    for game_name in all_games:
        items = results.get(game_name, [])
        if items:
            # Приоритет: позиция с наибольшим наличием
            item = max(items, key=lambda x: x.qty)
            cache[game_name] = {
                "qty": item.qty,
                "price": item.price,
                "updated_at": now,
            }
        else:
            cache[game_name] = {
                "qty": 0,
                "price": "",
                "updated_at": now,
            }
        updated += 1

    _save_cache(cache)
    logger.info(f"Обновление наличия завершено: {updated}/{len(all_games)} игр")
