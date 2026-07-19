from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from constants import PAGE_SIZE

# ── Постоянные клавиатуры ──────────────────────────────────────────────────────

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("🔍 Найти игру по названию")],
        [KeyboardButton("🤖 Подобрать игру (ИИ)")],
        [KeyboardButton("📚 Подборки")],
        [KeyboardButton("ℹ️ О нас")],
    ],
    resize_keyboard=True,
)

CANCEL_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ Отмена")]],
    resize_keyboard=True,
)

FINISH_AI_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ Завершить подбор")]],
    resize_keyboard=True,
)

FINISH_SEARCH_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ Завершить поиск")]],
    resize_keyboard=True,
)


# ── Динамические клавиатуры ────────────────────────────────────────────────────

def build_page_keyboard(
    page: int,
    total_items: int,
    prefix: str,
) -> InlineKeyboardMarkup | None:
    """
    Строит inline-клавиатуру пагинации.
    Возвращает None если страница всего одна.
    """
    total_pages = max(1, (total_items + PAGE_SIZE - 1) // PAGE_SIZE)
    if total_pages <= 1:
        return None

    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton("◀️ Назад", callback_data=f"{prefix}:{page - 1}")
        )
    if page < total_pages - 1:
        buttons.append(
            InlineKeyboardButton("Вперёд ▶️", callback_data=f"{prefix}:{page + 1}")
        )
    return InlineKeyboardMarkup([buttons]) if buttons else None
