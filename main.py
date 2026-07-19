"""
Точка входа. Запуск: python main.py
"""

import datetime
import logging
import logging.handlers
import sys
from pathlib import Path

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder, ContextTypes

from bot import register_handlers
from config import BOT_TOKEN
from paths import DATA_DIR
from services.availability import refresh_all
from services.catalog import load_catalog
from services.cloudshop_api import cloudshop_api
from services.gemini import setup_gemini


# ── Логирование ────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Консоль
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.INFO)

    # Файл с ротацией: 5 МБ × 3 файла
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(file_handler)

    # Глушим лишний шум от библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# ── Lifecycle-хуки ─────────────────────────────────────────────────────────────

BOT_DESCRIPTION = (
    "🎲 Hobby Games — ваш помощник по настольным играм! "
    "Найду игру по названию, подберу через ИИ под ваш запрос, "
    "покажу подборки с фото и проверю наличие в магазине. "
    "Даже если вы новичок — помогу выбрать идеальную игру для компании, семьи или друзей! 🎯"
)

BOT_SHORT_DESCRIPTION = (
    "🎲 Помощник по настольным играм: 🔍 поиск, 🤖 ИИ-подбор, 📚 подборки, 📦 наличие в магазине"
)


async def on_startup(app: Application) -> None:
    import asyncio
    logger = logging.getLogger(__name__)
    setup_gemini()
    catalog = load_catalog()
    logger.info(
        f"✅ Бот готов к работе. "
        f"Каталог: {len(catalog)} игр."
    )

    # Описание бота в профиле и «пузырёк»
    try:
        await app.bot.set_my_description(BOT_DESCRIPTION)
        await app.bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
        logger.info("✅ Описание бота установлено")
    except Exception as exc:
        logger.warning(f"Не удалось установить описание бота: {exc}")

    # Команды бота (отображаются в меню)
    try:
        await app.bot.set_my_commands([
            BotCommand("start", "Перезапустить бота"),
        ])
    except Exception as exc:
        logger.warning(f"Не удалось установить команды бота: {exc}")

    # Фоновое обновление наличия (не блокирует запуск)
    asyncio.create_task(_refresh_availability())


async def _refresh_availability() -> None:
    """Фоновая задача обновления кэша наличия."""
    logger = logging.getLogger(__name__)
    try:
        logger.info("🔄 Запуск обновления наличия...")
        await refresh_all()
        logger.info("✅ Обновление наличия завершено")
    except Exception as exc:
        logger.error(f"❌ Ошибка обновления наличия: {exc}")


async def _refresh_availability_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обёртка для job_queue — вызывает refresh_all по расписанию."""
    await _refresh_availability()


async def on_shutdown(app: Application) -> None:
    logger = logging.getLogger(__name__)
    logger.info("Завершение работы — закрываю соединения...")
    await cloudshop_api.close()
    logger.info("До свидания!")


# ── Запуск ─────────────────────────────────────────────────────────────────────

def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Запуск Hobby Games Bot...")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .read_timeout(120)
        .write_timeout(120)
        .connect_timeout(60)
        .pool_timeout(120)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    register_handlers(app)

    # Ежедневное обновление наличия в 3:00 ночи
    if app.job_queue:
        app.job_queue.run_daily(
            _refresh_availability_job,
            time=datetime.time(hour=3, minute=0),
            name="refresh_availability",
        )

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
