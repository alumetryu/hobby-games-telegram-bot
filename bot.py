"""
Регистрация всех обработчиков.
Импортируется из main.py и получает готовый Application.
"""

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from constants import AI_CHAT_STATE, SEARCH_CHAT_STATE, WAITING_AI_REQUEST, WAITING_GAME_NAME
from handlers.admin import cmd_clear_cache, cmd_reload_catalog, cmd_relogin, cmd_stats, cmd_status
from handlers.ai import ask_ai_request, handle_ai_chat, handle_ai_request
from handlers.catalog import catalog_callback, show_catalog
from handlers.search import ask_game_name, handle_search_continue, search_game, search_page_callback
from handlers.start import cmd_start, show_about


def register_handlers(app: Application) -> None:

    # ── ConversationHandler: поиск игры по названию ────────────────────────────
    search_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(r"^🔍 Найти игру по названию$"),
                ask_game_name,
            )
        ],
        states={
            WAITING_GAME_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, search_game)
            ],
            SEARCH_CHAT_STATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search_continue)
            ],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.Regex(r"^❌ Отмена$"), cmd_start),
            MessageHandler(filters.Regex(r"^❌ Завершить поиск$"), cmd_start),
        ],
    )

    # ── ConversationHandler: подбор через ИИ ──────────────────────────────────
    ai_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(r"^🤖 Подобрать игру \(ИИ\)$"),
                ask_ai_request,
            )
        ],
        states={
            WAITING_AI_REQUEST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_request)
            ],
            AI_CHAT_STATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_chat)
            ],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            MessageHandler(filters.Regex(r"^❌ Отмена$"), cmd_start),
            MessageHandler(filters.Regex(r"^❌ Завершить подбор$"), cmd_start),
        ],
    )

    # ── Команды ────────────────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("relogin", cmd_relogin))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("reload",  cmd_reload_catalog))
    app.add_handler(CommandHandler("clearcache", cmd_clear_cache))
    app.add_handler(CommandHandler("stats",    cmd_stats))

    # ── Диалоги ────────────────────────────────────────────────────────────────
    app.add_handler(search_conv)
    app.add_handler(ai_conv)

    # ── Обычные сообщения ──────────────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.Regex(r"^ℹ️ О нас$"), show_about))
    app.add_handler(MessageHandler(filters.Regex(r"^📚 Подборки$"), show_catalog))

    # ── Inline-кнопки каталога ──────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(catalog_callback, pattern=r"^(cat:|game:)"))

    # ── Inline-кнопки пагинации ────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(search_page_callback, pattern=r"^srch:"))
