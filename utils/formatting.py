"""
Утилиты форматирования текста.

Исправлен баг из оригинального бота:
  split_long_text() вызывалась ДВАЖДЫ на каждой итерации цикла —
  один раз для chunk, второй раз для вычисления индекса последнего элемента.
  Теперь список вычисляется один раз и передаётся как готовый объект.
"""

import re

from constants import PAGE_SIZE
from models import StockItem


def clean_game_name(raw: str) -> str:
    """Убирает Markdown-символы и лишние пробелы из названия игры."""
    cleaned = re.sub(r"[*_`#>🎲]", "", raw)
    return re.sub(r"\s+", " ", cleaned).strip()


def fmt_stock_item(item: StockItem) -> str:
    """Форматирует одну позицию из наличия."""
    price = item.price.strip() if item.price else ""
    price_str = (
        price if price and price not in ("0", "None", "none", "") else "не указана"
    )
    return (
        f"🎮 {item.name}\n"
        f"   📦 Количество: {item.qty} шт.\n"
        f"   💰 Цена: {price_str}"
    )


def split_long_text(text: str, limit: int = 4000) -> list[str]:
    """
    Разбивает длинный текст на части ≤ limit символов по границам абзацев.

    ВАЖНО: вызывайте один раз и сохраняйте результат в переменную.
    Не вызывайте дважды в одном цикле — в оригинале это был баг.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""

    for paragraph in text.split("\n\n"):
        addition = "\n\n" + paragraph if current else paragraph
        if len(current) + len(addition) > limit:
            if current:
                chunks.append(current)
            current = paragraph
        else:
            current = current + addition

    if current:
        chunks.append(current)

    return chunks


def build_search_page_text(
    items: list[StockItem],
    page: int,
    query: str,
) -> str:
    """Форматирует страницу результатов поиска."""
    total = len(items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    chunk = items[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    header = (
        f"🔎 <b>«{query}»</b> — найдено: {total} "
        f"(стр. {page + 1}/{total_pages})\n"
    )
    blocks: list[str] = [header]

    for item in chunk:
        if item.qty > 0:
            blocks.append(fmt_stock_item(item))
        else:
            blocks.append(f"🎮 {item.name}\n   ❌ Нет в наличии")

    return "\n\n".join(blocks)
