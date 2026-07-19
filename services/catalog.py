"""
Работа с Excel-каталогом игр.

Ожидаемая структура catalog.xlsx:
  Строка 1  — заголовок (пропускается)
  Колонка A — Название игры        (обязательно)
  Колонка B — Жанр/Тип             (опционально: стратегия, вечеринка, кооператив…)
  Колонка C — Возраст              (опционально: 8+, 10+, 12+…)
  Колонка D — Количество игроков   (опционально: 2-4, 3-6…)
  Колонка E — Теги / ключевые слова (опционально)

Фильтрация:
  Многоуровневый scoring по жанру, возрасту, числу игроков и словам в названии.
  Если ничего не найдено — возвращаем первые N записей (НЕ случайных!),
  чтобы ИИ получал хоть что-то вместо случайной выборки.
"""

import re
import logging
import os

import openpyxl

from config import EXCEL_CATALOG_PATH
from constants import GENRE_MAP
from models import Game

logger = logging.getLogger(__name__)


def load_catalog() -> list[Game]:
    """Читает каталог из Excel. Возвращает список Game."""
    path = EXCEL_CATALOG_PATH
    if not os.path.exists(path):
        logger.warning(f"Excel-каталог не найден: {path}")
        return []
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        games: list[Game] = []

        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                continue  # пропускаем заголовок

            def _cell(idx: int) -> str:
                return str(row[idx]).strip() if len(row) > idx and row[idx] else ""

            name = _cell(0)
            if not name or name.lower() in ("none", ""):
                continue

            games.append(
                Game(
                    name=name,
                    genre=_cell(1),
                    age=_cell(2),
                    players=_cell(3),
                    tags=_cell(4),
                    description=_cell(5),
                )
            )

        wb.close()
        logger.info(f"Каталог загружен: {len(games)} игр из «{path}»")
        return games

    except Exception as exc:
        logger.error(f"Ошибка чтения Excel: {exc}", exc_info=True)
        return []


def filter_catalog(
    catalog: list[Game],
    query: str,
    max_results: int = 80,
) -> list[Game]:
    """
    Фильтрует каталог по запросу с многоуровневым scoring.

    Веса:
      20 — прямое совпадение по тегам (основной критерий)
      15 — совпадение по жанру через GENRE_MAP
      10 — совпадение по возрасту
      10 — совпадение по числу игроков
       8 — слово из запроса найдено в названии игры

    Если ничего не нашли — возвращает первые max_results//2 записей,
    чтобы ИИ всё равно получил список для анализа.
    """
    q_lower = query.lower()
    q_words = [w for w in q_lower.split() if len(w) > 2]

    scored: list[tuple[Game, int]] = []

    for game in catalog:
        score = 0
        name_l   = game.name.lower()
        genre_l  = game.genre.lower()
        tags_l   = game.tags.lower()
        searchable = f"{name_l} {genre_l} {tags_l}"

        # ── Прямое совпадение по тегам (основной критерий) ────────────────
        if tags_l:
            tag_hits = 0
            for kw in q_words:
                if kw in tags_l:
                    tag_hits += 1
            if tag_hits > 0:
                score += 20 + tag_hits * 2

        # ── Жанровое совпадение ────────────────────────────────────────────
        for genre_key, kws in GENRE_MAP.items():
            if any(kw in q_lower for kw in kws):
                if (
                    genre_key in genre_l
                    or any(kw in searchable for kw in kws)
                ):
                    score += 15
                    break

        # ── Совпадение по возрасту ─────────────────────────────────────────
        if game.age:
            age_match = re.search(r"\b(\d+)(?:\s*[-–]\s*(\d+))?\s*(?:лет|год)", q_lower)
            if age_match:
                q_age_min = int(age_match.group(1))
                q_age_max = int(age_match.group(2)) if age_match.group(2) else q_age_min
                game_ages = [int(x) for x in re.findall(r"\d+", game.age)]
                if game_ages:
                    game_age_min = min(game_ages)
                    game_age_max = max(game_ages)
                    if q_age_min <= game_age_max and q_age_max >= game_age_min:
                        score += 10

        # ── Совпадение по числу игроков ────────────────────────────────────
        if game.players:
            players_in_query = re.search(
                r"\b(\d+)\s*(?:игрок|челов|чел\b|чела|человека)", q_lower
            )
            if players_in_query and players_in_query.group(1) in game.players:
                score += 10

        # ── Слова запроса в названии ───────────────────────────────────────
        for word in q_words:
            if word in name_l:
                score += 8

        if score > 0:
            scored.append((game, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    result = [g for g, _ in scored[:max_results]]

    if not result:
        fallback_n = max_results // 2
        logger.info(
            f"Фильтр не нашёл совпадений для «{query}» — "
            f"беру первые {fallback_n} игр из каталога"
        )
        result = catalog[:fallback_n]

    return result


def format_catalog_for_prompt(games: list[Game]) -> str:
    """Форматирует список игр для вставки в промпт Gemini."""
    lines: list[str] = []
    for i, g in enumerate(games, 1):
        parts = [g.name]
        if g.genre:
            parts.append(g.genre)
        if g.age:
            parts.append(g.age)
        if g.players:
            parts.append(g.players)
        if g.tags:
            parts.append(f"теги: {g.tags}")
        lines.append(f"{i}. {' | '.join(parts)}")
    return "\n".join(lines)


def find_replacement(
    catalog: list[Game],
    unavailable_name: str,
    available_names: set[str],
) -> Game | None:
    """
    Ищет замену недоступной игре из каталога.

    Логика:
      1. Находим недоступную игру в каталоге по названию.
      2. Скоринг по жанру, возрасту, числу игроков и тегам.
      3. Возвращаем кандидата с наивысшим скором.

    Возвращает Game или None, если замена не найдена.
    """
    target = None
    for g in catalog:
        if g.name.lower() == unavailable_name.lower():
            target = g
            break

    if not target:
        return None

    scored: list[tuple[Game, int]] = []

    for g in catalog:
        if g.name.lower() == unavailable_name.lower():
            continue
        if g.name.lower() in {n.lower() for n in available_names}:
            continue

        score = 0

        # Теги (вес 5 за каждый совпавший тег, минимум 20 при первом совпадении)
        if target.tags and g.tags:
            target_tags = set(target.tags.lower().replace(",", " ").split())
            game_tags = set(g.tags.lower().replace(",", " ").split())
            common = target_tags & game_tags
            if common:
                score += 20 + 5 * (len(common) - 1)

        # Жанр (вес 10)
        if target.genre and g.genre:
            target_genres = set(target.genre.lower().split("/"))
            game_genres = set(g.genre.lower().split("/"))
            if target_genres & game_genres:
                score += 10

        # Возраст (вес 10)
        if target.age and g.age:
            target_ages = [int(x) for x in re.findall(r"\d+", target.age)]
            game_ages = [int(x) for x in re.findall(r"\d+", g.age)]
            if target_ages and game_ages:
                if min(target_ages) <= max(game_ages) and max(target_ages) >= min(game_ages):
                    score += 10

        # Число игроков (вес 10)
        if target.players and g.players:
            target_nums = [int(x) for x in re.findall(r"\d+", target.players)]
            game_nums = [int(x) for x in re.findall(r"\d+", g.players)]
            if target_nums and game_nums:
                t_min, t_max = min(target_nums), max(target_nums)
                g_min, g_max = min(game_nums), max(game_nums)
                if t_min <= g_max and t_max >= g_min:
                    score += 10

        # Теги (вес 5 за каждый совпавший тег)
        if target.tags and g.tags:
            target_tags = set(target.tags.lower().replace(",", " ").split())
            game_tags = set(g.tags.lower().replace(",", " ").split())
            common = target_tags & game_tags
            if common:
                score += 5 * len(common)

        if score > 0:
            scored.append((g, score))

    if not scored:
        return None

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0][0]
