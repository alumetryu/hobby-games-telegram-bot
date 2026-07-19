"""
Обработчик каталога — inline-кнопки с категориями, карточки игр с фото.

Логика:
  1. Пользователь нажимает «📚 Каталог» → бот показывает категории.
  2. Пользователь выбирает категорию → бот отправляет игры по одной
     с фото, описанием и кнопками навигации.
  3. Кнопки: «Назад к категориям», «◀️ Пред.», «Следующая ▶️».
"""

import logging
import os
from pathlib import Path

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Update,
)
from telegram.ext import ContextTypes, ConversationHandler

from catalog_data import CATALOG
from services.availability import get_availability
from utils.keyboards import MAIN_MENU

logger = logging.getLogger(__name__)


def resolve_photo(photo: str):
    """Превращает строку photo в то, что понимает python-telegram-bot.

    - URL (http/https) → отдаём как есть
    - Telegram file_id (AgACAgIA...) → отдаём как есть
    - Локальный путь → открываем файл через open()
    """
    if not photo:
        return None
    if photo.startswith(("http://", "https://")):
        return photo
    if os.path.isfile(photo):
        return open(photo, "rb")
    return photo  # file_id


# ── Категории ──────────────────────────────────────────────────────────────────

async def show_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает меню категорий каталога."""
    buttons = []
    row: list[InlineKeyboardButton] = []
    for key, cat in CATALOG.items():
        btn = InlineKeyboardButton(
            cat["title"],
            callback_data=f"cat:{key}",
        )
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    # Кнопка «Назад» в главное меню
    buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="cat:home")])

    text = "📚 <b>Подборки настольных игр</b>\n\nВыберите категорию:"
    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return ConversationHandler.END


# ── Callback: выбор категории ──────────────────────────────────────────────────

async def catalog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатия inline-кнопок каталога."""
    query = update.callback_query
    await query.answer()

    data = query.data

    # ── Назад в главное меню ────────────────────────────────────────────────
    if data == "cat:home":
        await query.message.delete()
        await query.message.chat.send_message(
            "Выберите действие:",
            reply_markup=MAIN_MENU,
        )
        return

    # ── Назад к категориям ──────────────────────────────────────────────────
    if data == "cat:back":
        await _send_categories_menu(query)
        return

    # ── Показать игры в категории ───────────────────────────────────────────
    if data.startswith("cat:"):
        category_key = data.split(":", 1)[1]
        await _show_category_games(query, context, category_key, game_index=0)
        return

    # ── Навигация по играм ──────────────────────────────────────────────────
    if data.startswith("game:"):
        parts = data.split(":")
        category_key = parts[1]
        game_index = int(parts[2])
        await _show_category_games(query, context, category_key, game_index)
        return


# ── Вспомогательные функции ────────────────────────────────────────────────────

async def _send_categories_menu(query) -> None:
    """Редактирует сообщение, показывая категории."""
    buttons = []
    row: list[InlineKeyboardButton] = []
    for key, cat in CATALOG.items():
        btn = InlineKeyboardButton(
            cat["title"],
            callback_data=f"cat:{key}",
        )
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🏠 Главное меню", callback_data="cat:home")])

    text = "📚 <b>Подборки настольных игр</b>\n\nВыберите категорию:"
    try:
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception:
        # Если сообщение с фото — удаляем и отправляем новое
        await query.message.delete()
        await query.message.chat.send_message(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def _show_category_games(
    query, context: ContextTypes.DEFAULT_TYPE, category_key: str, game_index: int
) -> None:
    """Показывает одну игру из категории с фото и навигацией."""
    cat = CATALOG.get(category_key)
    if not cat:
        await query.answer("Категория не найдена", show_alert=True)
        return

    games = cat.get("games", [])
    if not games:
        await query.answer("В этой категории пока нет игр", show_alert=True)
        return

    if game_index < 0:
        game_index = 0
    if game_index >= len(games):
        game_index = len(games) - 1

    game = games[game_index]

    # ── Текст карточки ──────────────────────────────────────────────────────
    text_parts = [
        f"{cat['emoji']} <b>{game['name']}</b>",
        "",
        game.get("description", ""),
    ]

    # Проверяем наличие на сайте
    availability = await get_availability(game["name"])
    if availability:
        if availability.qty > 0:
            text_parts.append(f"\n📦 <b>В наличии: {availability.qty} шт</b>")
            if availability.price:
                text_parts.append(f"💰 <b>{availability.price}</b>")
        else:
            text_parts.append("\n❌ <b>Нет в наличии</b>")
    else:
        text_parts.append("\n❌ <b>Нет в наличии</b>")

    text = "\n".join(text_parts)

    # ── Кнопки навигации ────────────────────────────────────────────────────
    nav_row: list[InlineKeyboardButton] = []
    if game_index > 0:
        nav_row.append(
            InlineKeyboardButton(
                "◀️ Назад",
                callback_data=f"game:{category_key}:{game_index - 1}",
            )
        )
    nav_row.append(
        InlineKeyboardButton(
            "🏠 Категории",
            callback_data="cat:back",
        )
    )
    if game_index < len(games) - 1:
        nav_row.append(
            InlineKeyboardButton(
                "Вперёд ▶️",
                callback_data=f"game:{category_key}:{game_index + 1}",
            )
        )

    keyboard = InlineKeyboardMarkup([nav_row])
    photo = resolve_photo(game.get("photo", ""))

    # ── Навигация: редактируем текущее сообщение ────────────────────────────
    if query.data.startswith("game:"):
        try:
            if photo:
                media = InputMediaPhoto(media=photo, caption=text, parse_mode="HTML")
                await query.edit_message_media(media=media, reply_markup=keyboard)
            else:
                await query.edit_message_text(
                    text, parse_mode="HTML", reply_markup=keyboard
                )
            return
        except Exception:
            pass

    # ── Вход из категории: удаляем старое, шлём новое ───────────────────────
    try:
        await query.message.delete()
    except Exception:
        pass

    if photo:
        try:
            await query.message.chat.send_photo(
                photo=photo,
                caption=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            await query.message.chat.send_message(
                text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
    else:
        await query.message.chat.send_message(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
