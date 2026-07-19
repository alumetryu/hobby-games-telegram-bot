"""
Обработчик подбора игр через Gemini.

Логика работы:
  1. Загружаем каталог из Excel.
  2. Фильтруем по запросу (многоуровневый scoring).
  3. Отправляем в Gemini (без кэша — каждый раз разные подборки).
  4. Парсим названия игр из ответа (по эмодзи 🎲).
  5. ПАРАЛЛЕЛЬНО проверяем наличие всех игр на CloudShop.
  6. Если игра не в наличии — ищем замену из каталога.
  7. Формируем итоговый текст и отправляем.
"""

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from config import SHOP_TELEGRAM
from constants import AI_CHAT_SYSTEM, AI_RECOMMEND_PROMPT, AI_CHAT_STATE, WAITING_AI_REQUEST
from services.catalog import (
    filter_catalog,
    find_replacement,
    format_catalog_for_prompt,
    load_catalog,
)
from services.cloudshop import search_multiple
from services.gemini import ask_gemini
from models import Game, StockItem
from utils.formatting import clean_game_name, split_long_text
from utils.keyboards import CANCEL_KB, FINISH_AI_KB, MAIN_MENU

logger = logging.getLogger(__name__)

# Максимум обменов в памяти диалога
MAX_HISTORY_TURNS = 4

# Максимум попыток замены на одну игру
MAX_REPLACEMENT_ATTEMPTS = 3

AI_DISCLAIMER = "\n\n⚠️ Ответы ИИ могут быть неточными."


# ── Вспомогательные функции ────────────────────────────────────────────────────

def _parse_game_names(ai_response: str) -> list[str]:
    """Извлекает названия игр из ответа Gemini по маркеру 🎲."""
    names: list[str] = []
    for match in re.finditer(r"🎲\s*(.+?)(?:\n|$)", ai_response):
        name = clean_game_name(match.group(1).strip())
        if name:
            names.append(name)
    return names


def _normalize_game_names(names: list[str], catalog: list[Game]) -> list[str]:
    """
    Нормализует названия игр: сверяет с каталогом и возвращает
    оригинальное написание вместо КАПСОВОГО от Gemini.
    """
    lookup: dict[str, str] = {}
    for g in catalog:
        lookup[g.name.lower()] = g.name

    normalized: list[str] = []
    for name in names:
        original = lookup.get(name.lower())
        if original:
            normalized.append(original)
        else:
            # Нечёткий поиск: ищем по частичному совпадению
            found = False
            for low, orig in lookup.items():
                if name.lower() in low or low in name.lower():
                    normalized.append(orig)
                    found = True
                    break
            if not found:
                # Фоллбэк: Title Case для кириллицы
                normalized.append(name[0].upper() + name[1:].lower() if name else name)
    return normalized


def _fix_game_names_in_text(text: str, catalog: list[Game]) -> str:
    """
    Заменяет КАПСОВЫЕ названия игр в тексте ответа Gemini
    на оригинальные названия из каталога.
    """
    lookup: dict[str, str] = {}
    for g in catalog:
        lookup[g.name.lower()] = g.name

    # Ищем названия после 🎲 и заменяем
    def _replacer(match: re.Match) -> str:
        raw_name = clean_game_name(match.group(1).strip())
        original = lookup.get(raw_name.lower())
        if original:
            if original != raw_name:
                logger.info(f"Замена в тексте: '{raw_name}' -> '{original}'")
            return f"🎲 {original}"
        # Нечёткий поиск
        for low, orig in lookup.items():
            if raw_name.lower() in low or low in raw_name.lower():
                if orig != raw_name:
                    logger.info(f"Замена в тексте (fuzzy): '{raw_name}' -> '{orig}'")
                return f"🎲 {orig}"
        logger.warning(f"Игра не найдена в каталоге: '{raw_name}'")
        return match.group(0)

    return re.sub(r"🎲\s*(.+?)(?=\n|$)", _replacer, text)


def _build_stock_line(
    game_name: str,
    items: list[StockItem],
    tg_contact: str,
) -> str:
    """Строит строку о наличии для одной игры."""
    available = [p for p in items if p.qty > 0]
    if available:
        p = available[0]
        price = p.price.strip() if p.price else ""
        price_str = (
            price if price and price not in ("0", "None", "none", "")
            else "не указана"
        )
        return f"✅ В наличии: <b>{p.qty} шт.</b> | 💰 {price_str}"
    return f"❌ Нет в наличии — напишите {tg_contact} для предзаказа"


def _get_game_description(game: Game) -> str:
    """Генерирует краткое описание игры из данных каталога."""
    parts = []
    if game.genre:
        parts.append(f"📝 Жанр: {game.genre}")
    if game.age:
        parts.append(f"🎯 Возраст: {game.age}")
    if game.players:
        parts.append(f"👥 Игроки: {game.players}")
    return "\n".join(parts) if parts else ""


def _build_meta_line(game: Game | None, ai_block_text: str = "") -> str:
    """Строит строку метаданных: Игроки | Время | Сложность."""
    parts = []
    if game and game.players:
        parts.append(f"👥 {game.players}")
    if ai_block_text:
        time_match = re.search(
            r"⏱️\s*Время:\s*(.+?)(?:\s*\||\s*\n|$)", ai_block_text
        )
        if time_match:
            parts.append(f"⏱️ {time_match.group(1).strip()}")
        diff_match = re.search(
            r"(?:🎯\s*)?Сложность:\s*(.+?)(?:\s*\n|$)", ai_block_text
        )
        if diff_match:
            parts.append(f"🎯 {diff_match.group(1).strip()}")
    return "  |  ".join(parts) if parts else ""


def _resolve_description(
    game_name: str,
    catalog_dict: dict[str, Game],
    ai_block_text: str = "",
    ai_descriptions: dict[str, str] | None = None,
    replacement_name: str = "",
) -> str:
    """Возвращает лучшее доступное описание для игры (чистый текст без форматирования)."""
    target = replacement_name or game_name
    game_obj = catalog_dict.get(target.lower())
    desc = ""

    # 1) Ручное описание из каталога
    if game_obj and game_obj.description:
        desc = game_obj.description

    # 2) AI-описание из кэша (для замен)
    if not desc and ai_descriptions:
        tl = target.lower()
        for desc_name, desc_text in ai_descriptions.items():
            dl = desc_name.lower()
            if dl == tl or dl in tl or tl in dl:
                desc = desc_text
                break

    # 3) Описание из блока Gemini (текст после названия игры)
    if not desc and ai_block_text:
        lines = ai_block_text.split("\n")
        skip = ("👥 Игроки:", "⏱️ Время:", "🎯 Сложность:")
        desc_lines = []
        for raw_line in lines[1:]:
            stripped = raw_line.strip()
            if not stripped:
                continue
            if any(stripped.startswith(s) for s in skip):
                continue
            # Пропускаем строку, если она начинается с названия игры
            cleaned = clean_game_name(stripped)
            if cleaned and (cleaned.lower().startswith(target.lower())
                            or cleaned.lower().startswith(game_name.lower())):
                continue
            desc_lines.append(stripped)
        if desc_lines:
            desc = "\n".join(desc_lines)

    # 4) Генерируем из данных каталога
    if not desc and game_obj:
        desc = _get_game_description(game_obj)

    if not desc:
        desc = "🎯 Настольная игра"

    # Очищаем от любого форматирования
    desc = re.sub(r"<[^>]+>", "", desc)             # любые HTML-теги
    desc = re.sub(r"\*\*(.+?)\*\*", r"\1", desc)    # Markdown bold
    desc = re.sub(r"\*(.+?)\*", r"\1", desc)         # Markdown italic
    desc = re.sub(r"__(.+?)__", r"\1", desc)         # Markdown underline
    desc = re.sub(r"_(.+?)_", r"\1", desc)           # Markdown italic _

    # Убираем название игры из начала описания, если оно там дублируется
    desc_lines = desc.split("\n")
    if desc_lines:
        first = clean_game_name(desc_lines[0].strip())
        if first and (first.lower().startswith(target.lower())
                      or first.lower().startswith(game_name.lower())):
            desc = "\n".join(desc_lines[1:]).strip()

    return desc


async def _generate_replacement_descriptions(
    replacements: dict[str, str],
    catalog: list[Game],
) -> dict[str, str]:
    """
    Генерирует AI-описания для заменённых игр одним batch-запросом к Gemini.
    Возвращает {replacement_name: description}.
    """
    if not replacements:
        return {}

    catalog_dict: dict[str, Game] = {}
    if catalog:
        for g in catalog:
            catalog_dict[g.name.lower()] = g

    # Собираем информацию о заменах для промпта (пропускаем игры с ручным описанием)
    replacement_names = list(replacements.values())
    games_info: list[str] = []
    names_to_generate: list[str] = []
    for name in replacement_names:
        game = catalog_dict.get(name.lower())
        # Если у игры уже есть описание в каталоге — не генерируем
        if game and game.description:
            continue
        names_to_generate.append(name)
        info = name
        if game:
            parts = []
            if game.genre:
                parts.append(f"жанр: {game.genre}")
            if game.age:
                parts.append(f"возраст: {game.age}")
            if game.players:
                parts.append(f"игроки: {game.players}")
            if game.tags:
                parts.append(f"теги: {game.tags}")
            if parts:
                info += f" ({', '.join(parts)})"
        games_info.append(info)

    # Если у всех игр уже есть описания — не вызываем Gemini
    if not names_to_generate:
        return {}

    prompt = (
        "Ты — эксперт по настольным играм. Для каждой игры из списка напиши "
        "краткое описание (2-3 предложения), почему она подходит, чем интересна. "
        "Пиши живым языком, без вступлений. "
        "НЕ используй Markdown (**, *, _), HTML-теги или КАПС — только обычный текст.\n\n"
        "Формат ответа СТРОГО:\n"
        "Название игры\n"
        "Описание игры\n\n"
        "Игры:\n" + "\n".join(games_info)
    )

    try:
        response = await ask_gemini(prompt, max_tokens=1500)
        logger.info(f"Gemini replacement descriptions response: {response[:500]}")
    except Exception as exc:
        logger.error(f"Ошибка генерации описаний замен: {exc}", exc_info=True)
        return {}

    # Парсим ответ — разбиваем по двойным переносам, первая строка блока = название
    descriptions: dict[str, str] = {}
    blocks = re.split(r"\n\s*\n", response.strip())
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        first_line = clean_game_name(lines[0].strip())
        desc = "\n".join(lines[1:]).strip()

        # Ищем совпадение названия с играми для генерации
        for name in names_to_generate:
            # Нечёткое сравнение: нормализуем кавычки и проверяем вхождение
            fl = first_line.lower()
            nl = name.lower()
            # Заменяем все виды кавычек на единый символ
            _norm = lambda s: s.replace("«", "'").replace("»", "'").replace('"', "'").replace('"', "'").replace("ʺ", "'")
            fl_n = _norm(fl)
            nl_n = _norm(nl)
            if fl_n == nl_n or fl_n in nl_n or nl_n in fl_n:
                if desc:
                    descriptions[name] = desc
                break

    logger.info(f"Parsed replacement descriptions: {list(descriptions.keys())}")
    return descriptions


def _build_game_card(
    game_name: str,
    block: str,
    catalog_dict: dict[str, Game],
    stock_items: list[StockItem],
    tg_contact: str,
    ai_descriptions: dict[str, str] | None = None,
    replacement_from: str = "",
) -> str:
    """
    Собирает карточку одной игры.

    Если replacement_from задан — формат:
        ❌ Оригинал → 🎲 Замена
        Описание, мета, наличие
    """
    display_name = replacement_from or game_name

    desc = _resolve_description(
        game_name, catalog_dict, block, ai_descriptions, replacement_from,
    )
    target_game = catalog_dict.get(display_name.lower())
    meta = _build_meta_line(target_game, block)
    stock = _build_stock_line(display_name, stock_items, tg_contact)

    parts: list[str] = []

    if replacement_from:
        parts.append(f"❌ <b>{game_name}</b>  →  🎲 <b>{display_name}</b>")
    else:
        parts.append(f"🎲 <b>{display_name}</b>")

    if desc:
        parts.append("")
        parts.append(desc)
    if meta:
        parts.append(meta)
    parts.append(stock)

    return "\n".join(parts)


def _assemble_response(
    ai_response: str,
    stock_data: dict[str, list[StockItem]],
    user_query: str,
    tg_contact: str,
    replacements: dict[str, str] | None = None,
    catalog: list[Game] | None = None,
    ai_descriptions: dict[str, str] | None = None,
) -> str:
    """Собирает финальный текст: ответ ИИ + строки о наличии под каждой игрой."""
    header = f"🤖 <b>Подбор по запросу «{user_query}»</b>\n"
    blocks = re.split(r"(?=🎲)", ai_response.strip())
    blocks = [b.strip() for b in blocks if b.strip()]

    catalog_dict: dict[str, Game] = {}
    if catalog:
        for g in catalog:
            catalog_dict[g.name.lower()] = g

    stock_lower: dict[str, list[StockItem]] = {}
    for k, v in stock_data.items():
        stock_lower[k.lower()] = v

    replacements_lower: dict[str, str] = {}
    if replacements:
        for k, v in replacements.items():
            replacements_lower[k.lower()] = v

    def _get_stock_items(game_name: str) -> list[StockItem]:
        items = stock_data.get(game_name, [])
        if items:
            return items
        items = stock_lower.get(game_name.lower(), [])
        if items:
            return items
        for key_low, val in stock_lower.items():
            if game_name.lower() in key_low or key_low in game_name.lower():
                return val
        return []

    result_parts: list[str] = [header]

    replacement_targets: set[str] = set()
    if replacements:
        for v in replacements.values():
            replacement_targets.add(v.lower())

    processed_games: set[str] = set()

    for block in blocks:
        name_match = re.match(r"🎲\s*(.+)", block)
        if not name_match:
            continue

        game_name = clean_game_name(name_match.group(1).strip())

        if game_name.lower() in replacement_targets:
            continue
        if game_name.lower() in processed_games:
            continue
        processed_games.add(game_name.lower())

        # Ищем замену
        replacement_name = ""
        if replacements:
            replacement_name = replacements.get(game_name, "")
            if not replacement_name:
                replacement_name = replacements_lower.get(game_name.lower(), "")
            if not replacement_name:
                for rkey, rval in replacements.items():
                    if rkey.lower() in game_name.lower() or game_name.lower() in rkey.lower():
                        replacement_name = rval
                        break

        if replacement_name:
            items = _get_stock_items(replacement_name)
            card = _build_game_card(
                game_name, block, catalog_dict, items, tg_contact,
                ai_descriptions, replacement_from=replacement_name,
            )
        else:
            items = _get_stock_items(game_name)
            card = _build_game_card(
                game_name, block, catalog_dict, items, tg_contact,
                ai_descriptions,
            )

        result_parts.append(card)

    return "\n\n".join(result_parts)


def _tg_contact(raw: str) -> str:
    return raw if raw.startswith("@") else f"@{raw}"


async def _send_chunks(update: Update, text: str, final_kb=None) -> None:
    """
    Разбивает текст на части и отправляет.
    split_long_text() вызывается ОДИН раз — результат в переменную.
    """
    chunks = split_long_text(text)
    last_idx = len(chunks) - 1
    for i, chunk in enumerate(chunks):
        kb = final_kb if i == last_idx else None
        await update.message.reply_text(chunk, parse_mode="HTML", reply_markup=kb)


async def _check_availability_with_replacements(
    game_names: list[str],
    catalog: list[Game],
    msg,
) -> tuple[dict[str, list[StockItem]], dict[str, str]]:
    """
    Проверяет наличие игр на сайте и ищет замены для недоступных.

    Возвращает:
      - stock_data: {название: [StockItem]} — данные о наличии
      - replacements: {оригинал: замена} — маппинг замен
    """

    # Шаг 1: Проверяем наличие всех игр
    stock_data = await search_multiple(game_names)

    # Шаг 2: Находим недоступные игры
    unavailable = []
    for name in game_names:
        items = stock_data.get(name, [])
        if not items or all(p.qty == 0 for p in items):
            unavailable.append(name)

    if not unavailable:
        return stock_data, {}

    await msg.edit_text(
        f"🔄 {len(unavailable)} игр нет в наличии, подбираю замены..."
    )

    # Шаг 3: Ищем замены для недоступных игр
    replacements: dict[str, str] = {}
    available_names = set()

    for name in unavailable:
        for attempt in range(MAX_REPLACEMENT_ATTEMPTS):
            replacement = find_replacement(catalog, name, available_names)
            if not replacement:
                break

            # Проверяем наличие замены
            replacement_stock = await search_multiple([replacement.name])
            replacement_items = replacement_stock.get(replacement.name, [])

            if replacement_items and any(p.qty > 0 for p in replacement_items):
                replacements[name] = replacement.name
                available_names.add(replacement.name)
                stock_data[replacement.name] = replacement_items
                logger.info(f"Замена: '{name}' -> '{replacement.name}'")
                break
            else:
                # Замена тоже недоступна — ищем следующую
                available_names.add(replacement.name)

    return stock_data, replacements


# ── Точка входа: первый запрос ─────────────────────────────────────────────────

async def ask_ai_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "🤖 <b>Подбор игры с помощью ИИ</b>\n\n"
        "Опишите что ищете, например:\n"
        "• «Весёлая игра для компании 5-6 человек»\n"
        "• «Стратегия для двоих, не слишком сложная»\n"
        "• «Что-нибудь для детей 8 лет»\n\n"
        "Напишите ваш запрос:",
        parse_mode="HTML",
        reply_markup=CANCEL_KB,
    )
    return WAITING_AI_REQUEST


async def handle_ai_request(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    user_input = update.message.text.strip()

    if user_input == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=MAIN_MENU)
        return ConversationHandler.END

    tg = _tg_contact(SHOP_TELEGRAM)
    msg = await update.message.reply_text("📖 Читаю каталог игр...")

    # 1. Загружаем каталог
    catalog = load_catalog()
    if not catalog:
        await msg.edit_text(
            "❌ Каталог не найден.\n"
            "Убедитесь что файл <b>catalog.xlsx</b> находится рядом с ботом.",
            parse_mode="HTML",
        )
        await update.message.reply_text("Выберите действие:", reply_markup=MAIN_MENU)
        return ConversationHandler.END

    await msg.edit_text("🔍 Анализирую запрос...")

    # 2. Фильтруем каталог
    filtered = filter_catalog(catalog, user_input, max_results=80)

    # 3. Формируем промпт и запрашиваем Gemini (кэш отключён для разнообразия)
    await msg.edit_text(f"🤖 Подбираю из {len(filtered)} игр...")
    catalog_text = format_catalog_for_prompt(filtered)
    prompt = AI_RECOMMEND_PROMPT.format(
        count=len(filtered),
        catalog=catalog_text,
        query=user_input,
    )
    try:
        ai_response = await ask_gemini(prompt, max_tokens=2000)
    except Exception as exc:
        logger.error(f"Ошибка Gemini: {exc}", exc_info=True)
        await msg.edit_text("❌ ИИ временно недоступен. Попробуйте позже.")
        await update.message.reply_text("Выберите действие:", reply_markup=MAIN_MENU)
        return ConversationHandler.END

    game_names = _parse_game_names(ai_response)
    game_names = _normalize_game_names(game_names, catalog)
    ai_response = _fix_game_names_in_text(ai_response, catalog)

    # 4. Параллельная проверка наличия + замены
    await msg.edit_text(
        f"🔍 Проверяю наличие {len(game_names)} игр на сайте..."
    )
    stock_data, replacements = await _check_availability_with_replacements(
        game_names, catalog, msg
    )
    logger.info(
        f"Наличие: { {k: len(v) for k, v in stock_data.items()} }, "
        f"замены: {replacements}"
    )

    # Генерируем AI-описания для заменённых игр
    ai_descriptions: dict[str, str] = {}
    if replacements:
        await msg.edit_text("✍️ Генерирую описания для замен...")
        ai_descriptions = await _generate_replacement_descriptions(
            replacements, catalog
        )

    # 6. Собираем финальный текст
    full_text = _assemble_response(
        ai_response, stock_data, user_input, tg, replacements, catalog,
        ai_descriptions
    )

    # 8. Сохраняем историю диалога
    context.user_data["ai_history"] = [
        {"role": "system", "content": AI_CHAT_SYSTEM},
        {"role": "user", "content": user_input},
        {"role": "assistant", "content": ai_response},
    ]

    full_text += AI_DISCLAIMER

    await msg.delete()
    await _send_chunks(update, full_text, final_kb=FINISH_AI_KB)
    return AI_CHAT_STATE


# ── Продолжение диалога ────────────────────────────────────────────────────────

async def handle_ai_chat(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    user_msg = update.message.text.strip()

    if user_msg == "❌ Завершить подбор":
        context.user_data.pop("ai_history", None)
        await update.message.reply_text(
            "✅ Диалог завершён. Выберите действие:",
            reply_markup=MAIN_MENU,
        )
        return ConversationHandler.END

    history: list[dict] = context.user_data.get("ai_history", [])
    if not history:
        await update.message.reply_text(
            "Сессия истекла. Начните подбор заново.",
            reply_markup=MAIN_MENU,
        )
        return ConversationHandler.END

    tg = _tg_contact(SHOP_TELEGRAM)
    msg = await update.message.reply_text("🤖 Думаю...")

    # Загружаем каталог для поиска замен и для промпта
    catalog = load_catalog()
    catalog_text = format_catalog_for_prompt(catalog) if catalog else ""

    # Берём системный промпт + последние MAX_HISTORY_TURNS обменов
    system = history[0]["content"]
    tail = history[1:][-MAX_HISTORY_TURNS * 2:]  # user + assistant пары

    # Формируем промпт с каталогом
    prompt_parts = [system]
    if catalog_text:
        prompt_parts.append(f"КАТАЛОГ ИГР:\n{catalog_text}")
    for h in tail:
        prefix = "Пользователь" if h["role"] == "user" else "Ассистент"
        prompt_parts.append(f"{prefix}: {h['content']}")
    prompt_parts.append(f"Пользователь: {user_msg}")

    try:
        ai_text = await ask_gemini("\n\n".join(prompt_parts), max_tokens=800)
    except Exception as exc:
        logger.error(f"Ошибка Gemini chat: {exc}", exc_info=True)
        await msg.edit_text("❌ Ошибка ИИ. Попробуйте переформулировать вопрос.")
        return AI_CHAT_STATE

    # Если ИИ рекомендует игры — проверяем наличие + замены
    game_names = _parse_game_names(ai_text)
    if game_names:
        game_names = _normalize_game_names(game_names, catalog)
        ai_text = _fix_game_names_in_text(ai_text, catalog)
        # Ограничиваем до 2 игр
        game_names = game_names[:2]
        await msg.edit_text("🔍 Проверяю наличие на сайте...")
        stock_data, replacements = await _check_availability_with_replacements(
            game_names, catalog, msg
        )
        # Генерируем AI-описания для заменённых игр
        ai_descriptions: dict[str, str] = {}
        if replacements:
            await msg.edit_text("✍️ Генерирую описания для замен...")
            ai_descriptions = await _generate_replacement_descriptions(
                replacements, catalog
            )
        final_text = _assemble_response(
            ai_text, stock_data, user_msg, tg, replacements, catalog,
            ai_descriptions
        )
        final_text += AI_DISCLAIMER
    else:
        final_text = ai_text

    # Обновляем историю (ограничиваем размер)
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": ai_text})
    context.user_data["ai_history"] = [history[0]] + history[1:][-MAX_HISTORY_TURNS * 2:]

    await msg.delete()
    await _send_chunks(update, final_text, final_kb=FINISH_AI_KB)
    return AI_CHAT_STATE
