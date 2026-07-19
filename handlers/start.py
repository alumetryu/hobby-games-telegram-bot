import logging

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from config import SHOP_ADDRESS, SHOP_PHONE, SHOP_TELEGRAM
from services.user_tracker import record_user
from utils.keyboards import MAIN_MENU

logger = logging.getLogger(__name__)


WELCOME_TEXT = (
    "🎲 <b>Добро пожаловать в Hobby Games!</b>\n\n"
    "👋 Я ваш персональный помощник по настольным играм. "
    "Даже если вы никогда не играли — я помогу разобраться и найти то, что подойдёт именно вам.\n\n"
    "🎯 <b>Вот что я умею:</b>\n\n"
    "🔍 <b>Поиск по названию</b> — знаете как называется игра? Просто напишите, и я покажу, есть ли она в наличии и сколько стоит.\n\n"
    "🤖 <b>Подбор через ИИ</b> — расскажите мне, сколько людей будет играть, какой возраст и что вам нравится, "
    "а я подберу несколько вариантов из нашего каталога и сразу проверю, что есть в магазине.\n\n"
    "📚 <b>Подборки по категориям</b> — если хотите просто посмотреть, что у нас есть, загляните в раздел «Подборки» — "
    "там игры разбиты по жанрам с фотографиями и описаниями.\n\n"
    "📍 <b>Информация о магазине</b> — адрес, телефон и контакты для связи.\n\n"
    "👇 <b>Начните с выбора действия в меню ниже — я подскажу на каждом шаге!</b>"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    record_user(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "",
        last_name=user.last_name or "",
    )

    await update.message.reply_text(
        WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )
    return ConversationHandler.END


async def show_about(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg = (
        SHOP_TELEGRAM
        if SHOP_TELEGRAM.startswith("@")
        else f"@{SHOP_TELEGRAM}"
    )
    await update.message.reply_text(
        f"🏪 <b>Hobby Games</b>\n\n"
        f"📍 Адрес: {SHOP_ADDRESS or 'уточняйте в магазине'}\n"
        f"📞 Телефон: {SHOP_PHONE or 'уточняйте в магазине'}\n"
        f"💬 Telegram: {tg}\n\n"
        "Рады помочь с выбором настольной игры! 🎲",
        parse_mode="HTML",
        reply_markup=MAIN_MENU,
    )
