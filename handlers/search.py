import logging

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from constants import SEARCH_CHAT_STATE, WAITING_GAME_NAME
from models import StockItem
from services.cloudshop import search_in_cloudshop
from utils.formatting import build_search_page_text
from utils.fuzzy import correct_game_name
from utils.keyboards import CANCEL_KB, FINISH_SEARCH_KB, MAIN_MENU, build_page_keyboard

logger = logging.getLogger(__name__)


async def ask_game_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 1: просим название игры."""
    await update.message.reply_text(
        "🔍 Введите название игры:",
        reply_markup=CANCEL_KB,
    )
    return WAITING_GAME_NAME


async def _do_search(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> int:
    """Общая логика поиска — вызывается из search_game и handle_search_continue."""
    msg = await update.message.reply_text(
        f"⏳ Ищу <b>«{text}»</b>...", parse_mode="HTML"
    )

    correction = correct_game_name(text)
    if correction:
        await msg.edit_text(
            f"🔍 Нашёл похожее: <b>«{correction}»</b>\n"
            f"Ищу по исправленному названию...",
            parse_mode="HTML",
        )
        text = correction

    try:
        products = await search_in_cloudshop(text)
    except Exception as exc:
        logger.error(f"Ошибка поиска '{text}': {exc}", exc_info=True)
        await msg.edit_text("❌ Ошибка при поиске. Попробуйте позже.")
        await update.message.reply_text(
            "Введите название следующей игры или завершите поиск:",
            reply_markup=FINISH_SEARCH_KB,
        )
        return SEARCH_CHAT_STATE

    await msg.delete()

    if not products:
        await update.message.reply_text(
            f"❌ <b>«{text}»</b> не найдена в каталоге.\n\n"
            "Попробуйте другой запрос.",
            parse_mode="HTML",
            reply_markup=FINISH_SEARCH_KB,
        )
        return SEARCH_CHAT_STATE

    context.user_data["search_items"] = [
        {"name": p.name, "qty": p.qty, "price": p.price} for p in products
    ]
    context.user_data["search_query"] = text

    page_text = build_search_page_text(products, page=0, query=text)
    kb = build_page_keyboard(page=0, total_items=len(products), prefix="srch")

    await update.message.reply_text(page_text, parse_mode="HTML", reply_markup=kb)
    await update.message.reply_text(
        "Введите название следующей игры или завершите поиск:",
        reply_markup=FINISH_SEARCH_KB,
    )
    return SEARCH_CHAT_STATE


async def search_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Шаг 2: ищем на CloudShop и выводим результат с пагинацией."""
    text = update.message.text.strip()

    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=MAIN_MENU)
        return ConversationHandler.END

    return await _do_search(update, context, text)


async def handle_search_continue(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Продолжение поиска: пользователь вводит новое название после результатов."""
    text = update.message.text.strip()

    if text == "❌ Завершить поиск":
        context.user_data.pop("search_items", None)
        context.user_data.pop("search_query", None)
        await update.message.reply_text(
            "✅ Поиск завершён. Выберите действие:",
            reply_markup=MAIN_MENU,
        )
        return ConversationHandler.END

    return await _do_search(update, context, text)


async def search_page_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Обработчик inline-кнопок пагинации (srch:<page>)."""
    query = update.callback_query
    await query.answer()

    page = int(query.data.split(":")[1])
    raw_items: list[dict] = context.user_data.get("search_items", [])
    search_query: str = context.user_data.get("search_query", "")

    if not raw_items:
        await query.edit_message_text("❌ Данные устарели. Повторите поиск.")
        return

    items = [StockItem(**i) for i in raw_items]
    page_text = build_search_page_text(items, page=page, query=search_query)
    kb = build_page_keyboard(page=page, total_items=len(items), prefix="srch")

    await query.edit_message_text(page_text, parse_mode="HTML", reply_markup=kb)
