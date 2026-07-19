import logging
from functools import wraps
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_IDS
from services.catalog import load_catalog
from services.cloudshop_api import cloudshop_api
from services.gemini import GEMINI_MODEL
from services.user_tracker import get_stats
from utils.keyboards import MAIN_MENU

logger = logging.getLogger(__name__)


def admin_only(func: Callable) -> Callable:
    """Декоратор: команда доступна только пользователям из ADMIN_IDS."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid not in ADMIN_IDS:
            await update.message.reply_text("⛔ Нет доступа.")
            logger.warning(f"Попытка admin-команды от {uid}")
            return
        return await func(update, context)
    return wrapper


@admin_only
async def cmd_relogin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Принудительный перезаход в CloudShop API."""
    msg = await update.message.reply_text("🔄 Переподключаюсь к CloudShop...")
    try:
        cloudshop_api._logged_in = False
        await cloudshop_api._ensure_session()
        await msg.edit_text("✅ Переподключился успешно!")
    except Exception as exc:
        logger.error(f"Ошибка relogin: {exc}", exc_info=True)
        await msg.edit_text(
            "❌ Ошибка переподключения.\n"
            "Проверьте CS_EMAIL и CS_PASSWORD в файле .env"
        )


@admin_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статус бота: API, каталог, ИИ-модель."""
    catalog = load_catalog()
    api_ok = cloudshop_api._logged_in

    await update.message.reply_text(
        f"<b>📊 Статус бота</b>\n\n"
        f"🌐 CloudShop API: {'✅ подключён' if api_ok else '❌ не подключён'}\n"
        f"📋 Игр в каталоге: <b>{len(catalog)}</b>\n"
        f"🤖 Модель ИИ: <b>{GEMINI_MODEL}</b>",
        parse_mode="HTML",
    )


@admin_only
async def cmd_reload_catalog(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Перезагружает каталог из Excel."""
    catalog = load_catalog()
    await update.message.reply_text(
        f"✅ Каталог перезагружен: <b>{len(catalog)}</b> игр.",
        parse_mode="HTML",
    )


@admin_only
async def cmd_clear_cache(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Очищает кэш ИИ-рекомендаций."""
    import os
    cache_file = "ai_cache.db"
    if os.path.exists(cache_file):
        os.remove(cache_file)
        await update.message.reply_text("🗑️ Кэш ИИ очищен.")
    else:
        await update.message.reply_text("ℹ️ Кэш пуст — нечего очищать.")


@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает статистику использования бота."""
    stats = get_stats()
    await update.message.reply_text(
        f"<b>📈 Статистика бота</b>\n\n"
        f"👤 Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"🟢 Активны сегодня: <b>{stats['active_today']}</b>\n"
        f"💬 Всего взаимодействий: <b>{stats['total_interactions']}</b>",
        parse_mode="HTML",
    )
