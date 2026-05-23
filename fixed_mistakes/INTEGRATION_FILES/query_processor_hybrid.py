"""
NLP модуль PriceHunt — ГИБРИДНАЯ ВЕРСИЯ.

Оригинал: pymorphy3 + rapidfuzz + ручные словари (PriceHunt)
Добавлено: BART algorithmic fallback (n-gram + phonetic + weighted Levenshtein)
           + опциональный BART neural + hot-reload dictionary + metrics.

Совместимость: публичный API полностью совпадает с оригиналом.
               process_query(), correct_query(), expand_synonyms() и т.д.
"""

import logging
import re
from functools import lru_cache
from typing import Optional, Tuple, Dict, List

from app.nlp.hybrid_spell_checker import HybridSpellChecker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Гибридный spell-checker (singleton)
# ---------------------------------------------------------------------------

_spell_checker = HybridSpellChecker(
    use_gpu=False,
    config_path="app/nlp/config_hybrid.yaml",
    auto_update=True,
)

# ---------------------------------------------------------------------------
# Публичный API (обратная совместимость с PriceHunt)
# ---------------------------------------------------------------------------

def correct_query(query: str) -> Tuple[str, bool]:
    """
    Исправляет опечатки в запросе.
    Возвращает (исправленный_запрос, был_ли_исправлен).
    """
    result = _spell_checker.process_query(query)
    return result["corrected"], result["was_corrected"]


def expand_synonyms(query: str) -> List[str]:
    """
    Возвращает список вариантов запроса с учётом синонимов.
    Максимум 4 варианта.
    """
    return _spell_checker.expand_synonyms(query)[:4]


def used_synonyms(query: str) -> Dict[str, List[str]]:
    """Возвращает синонимы, которые реально применимы к запросу."""
    return _spell_checker.used_synonyms(query)


def detect_category(query: str) -> str:
    """Определяет категорию товара по запросу."""
    return _spell_checker._detect_category(query)


def process_query(raw_query: str) -> dict:
    """
    Полный pipeline обработки поискового запроса.

    Возвращает:
      original — исходный запрос
      corrected — исправленный запрос
      was_corrected — True если было исправление
      search_variants — список вариантов для поиска
      primary_query — основной запрос для парсеров
      category — категория товара
    """
    return _spell_checker.process_query(raw_query)


# ---------------------------------------------------------------------------
# Legacy: pymorphy3 (lazy init, если hybrid не справился с чем-то специфичным)
# ---------------------------------------------------------------------------

_morph = None


def _get_morph():
    global _morph
    if _morph is None:
        try:
            import pymorphy3
            _morph = pymorphy3.MorphAnalyzer()
            logger.info("[NLP] pymorphy3 loaded (legacy)")
        except ImportError:
            logger.warning("[NLP] pymorphy3 not installed")
    return _morph


@lru_cache(maxsize=4096)
def lemmatize_word(word: str) -> str:
    """Возвращает начальную форму слова через pymorphy3."""
    morph = _get_morph()
    if morph is None:
        return word
    try:
        parsed = morph.parse(word)
        if parsed:
            return parsed[0].normal_form
    except Exception:
        pass
    return word


def lemmatize_query(query: str) -> str:
    """Приводит каждое слово запроса к начальной форме."""
    words = query.lower().split()
    return " ".join([lemmatize_word(w) for w in words])
