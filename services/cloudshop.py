"""
CloudShop — поиск игр через прямые HTTP-запросы к API.

Данные получаются из REST API CloudShop, а не через парсинг DOM.
Каталог загружается один раз, поиск фильтруется на стороне Python.
"""

import logging
import re

from models import StockItem
from services.cloudshop_api import cloudshop_api

logger = logging.getLogger(__name__)


def _clean_query(query: str) -> str:
    """
    Убирает из запроса части, которые мешают поиску:

      • ", арт. XXXXX"  — артикулы
      • "Настольная игра: " / "Настольная игра " — стандартный префикс

    Примеры:
      "Настольная игра: 7 Чудес, арт. 7Wonders"  → "7 Чудес"
      "Настольная игра: Каркассон, арт. 10002"    → "Каркассон"
      "Настольная игра Пылающее сердце"           → "Пылающее сердце"
      "Находка для шпиона (2-е рус. изд.)"        → без изменений
    """
    q = re.sub(r",?\s*арт\..*$", "", query, flags=re.IGNORECASE).strip()
    q = re.sub(r"^Настольная\s+игра\s*:\s*", "", q, flags=re.IGNORECASE).strip()
    q = re.sub(r"^Настольная\s+игра\s+", "", q, flags=re.IGNORECASE).strip()
    q = q.strip('«»"\'')
    return q or query


def _name_matches(card_name: str, query: str) -> bool:
    """
    Проверяет, подходит ли товар под поисковый запрос.

    Логика:
      1. Точное совпадение.
      2. Запрос целиком содержится в названии как подстрока.
      3. Название целиком содержится в запросе.
      4. Хотя бы одно слово запроса (длина > 2) найдено в названии
         как отдельное слово (с проверкой границ слов).
    """
    q = query.lower().strip()
    n = card_name.lower().strip()

    if q == n:
        return True

    if q in n:
        return True

    if n in q:
        return True

    q_words = [w for w in q.split() if len(w) > 2]
    if not q_words:
        return True

    for w in q_words:
        pat = r"(?<!\w)" + re.escape(w) + r"(?!\w)"
        if re.search(pat, n):
            return True

    return False


def _is_exact_catalog_match(card_name: str, catalog_name: str) -> bool:
    """
    Строгая проверка: карточка CloudShop точно соответствует названию из каталога.

    Логика:
      - Нормализуем оба имени (убираем «Настольная игра: », кавычки, регистр).
      - Все слова из каталога ДОЛЖНЫ присутствовать в карточке.
      - Если в каталоге ЕСТЬ слова-маркеры версии (редакция, издание и т.д.),
        они ДОЛЖНЫ быть и в карточке.
      - Если в карточке ЕСТЬ маркеры версии, которых нет в каталоге — это другая
        версия, не совпадает.
    """
    def _norm(s: str) -> str:
        s = re.sub(r"^Настольная\s+игра\s*:\s*", "", s, flags=re.IGNORECASE)
        s = s.strip('«»"\' ().,')
        return s.lower().strip()

    cn = _norm(card_name)
    qn = _norm(catalog_name)

    if cn == qn:
        return True

    q_words = [w for w in qn.split() if len(w) > 1 or w.isdigit()]
    if not q_words:
        return False

    if not all(w in cn for w in q_words):
        return False

    version_markers = [
        "редакция", "ред.", "издание", "изд.", "дополнение", "доп.",
        "расширение", "deluxe", "collector", "ltd", "premium",
        "большая", "малая", "путешествие", "за гранью",
    ]

    for marker in version_markers:
        if marker in cn and marker not in qn:
            return False

    for marker in version_markers:
        if marker in qn and marker not in cn:
            return False

    return True


def _format_price(raw) -> str:
    """Форматирует цену из API в читаемый вид: 199000 → '199 000,00 UZS'."""
    try:
        value = int(raw)
    except (ValueError, TypeError):
        return str(raw) if raw else ""
    if value <= 0:
        return ""
    formatted = f"{value:,}".replace(",", " ")
    return f"{formatted},00 UZS"


async def search_in_cloudshop(query: str) -> list[StockItem]:
    """
    Ищет товар по названию через CloudShop API.
    Возвращает все совпадения из каталога.
    """
    clean = _clean_query(query)
    if clean != query:
        logger.debug(f"Запрос очищен: '{query}' → '{clean}'")

    raw = await cloudshop_api.search(clean)

    results = [
        StockItem(
            name=item["name"],
            qty=item["qty"],
            price=_format_price(item["price"]),
        )
        for item in raw
        if _name_matches(item["name"], clean)
    ]

    logger.info(
        f"Поиск '{clean}': {len(results)} совпадений из {len(raw)} товаров"
    )
    return results


async def search_exact_in_cloudshop(game_name: str) -> StockItem | None:
    """
    Ищет товар по названию.
    Очищает название от артикулов и префикса "Настольная игра" перед поиском.
    Сравнивает результат с очищенным названием.
    """
    clean = _clean_query(game_name)
    raw = await cloudshop_api.search(clean)

    target = re.sub(r"\s+", " ", clean.strip().lower())

    for item in raw:
        card = re.sub(r"\s+", " ", item["name"].strip().lower())
        if card == target or target in card or card in target:
            return StockItem(
                name=item["name"].strip(),
                qty=item["qty"],
                price=_format_price(item["price"]),
            )

    logger.info(f"Не найдено: '{game_name}' (cleaned: '{clean}')")
    return None


async def search_multiple(
    game_names: list[str],
) -> dict[str, list[StockItem]]:
    """
    Проверяет наличие нескольких игр за одну загрузку каталога.
    Каталог загружается ОДИН раз, фильтрация — на Python.
    Для каждого имени сначала ищет ТОЧНОЕ совпадение с каталогом.
    """
    if not game_names:
        return {}

    logger.info(f"Параллельный поиск {len(game_names)} игр")

    try:
        raw_results = await cloudshop_api.search_multiple(game_names)
    except Exception as exc:
        logger.error(f"Ошибка API: {exc}", exc_info=True)
        return {name: [] for name in game_names}

    output: dict[str, list[StockItem]] = {}
    for name in game_names:
        items = raw_results.get(name, [])
        results = [
            StockItem(
                name=item["name"],
                qty=item["qty"],
                price=_format_price(item["price"]),
            )
            for item in items
            if _name_matches(item["name"], _clean_query(name))
        ]

        # Собираем все точные совпадения
        exact = [
            item for item in results
            if _is_exact_catalog_match(item.name, name)
        ]

        if exact:
            output[name] = exact
            logger.info(
                f"Поиск '{name}': {len(exact)} точных совпадений"
            )
        else:
            output[name] = results
            logger.info(
                f"Поиск '{name}': {len(results)} нечётких совпадений "
                f"(точного нет)"
            )

    return output
