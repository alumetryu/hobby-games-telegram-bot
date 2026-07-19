import logging
import re
from rapidfuzz import fuzz

from services.catalog import load_catalog

logger = logging.getLogger(__name__)

_clean_cache: list[str] | None = None


def _strip_article(name: str) -> str:
    n = re.sub(r",?\s*арт\.?\s*\S+$", "", name, flags=re.IGNORECASE).strip()
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n).strip()
    return n


def _get_clean_names() -> list[str]:
    global _clean_cache
    if _clean_cache is None:
        catalog = load_catalog()
        seen: set[str] = set()
        names: list[str] = []
        for g in catalog:
            clean = _strip_article(g.name)
            low = clean.lower()
            if low not in seen:
                seen.add(low)
                names.append(clean)
        _clean_cache = names
    return _clean_cache


def correct_game_name(query: str) -> str | None:
    names = _get_clean_names()
    if not names:
        return None

    q = query.strip()
    ql = q.lower()
    q_words = ql.split()
    best_score = 0.0
    best_name = ""

    for name in names:
        nl = name.lower()

        if ql == nl:
            return None

        if len(q_words) > 1 and len(q_words) < len(nl.split()):
            match = all(q_words[i] == nl.split()[i] for i in range(len(q_words)))
            if match and len(q) < len(name) * 0.5:
                continue

        r = fuzz.ratio(ql, nl)
        ts = fuzz.token_sort_ratio(ql, nl)
        score = max(r, ts)

        if score > best_score:
            best_score = score
            best_name = name

    if best_score == 100 and len(q) >= len(best_name):
        return None

    if len(q_words) == 1:
        for name in names:
            nl = name.lower()
            n_words = nl.split()
            if len(n_words) > 1 and q_words[0] == n_words[0]:
                return None

    threshold = 85 if len(q) <= 6 else 80

    if best_score >= threshold:
        logger.info(f"Fuzzy correct: '{query}' -> '{best_name}' ({best_score:.0f}%)")
        return best_name

    return None
