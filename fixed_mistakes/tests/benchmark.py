"""
Бенчмарки для сравнения скорости разных методов.

Запуск:
    pytest tests/benchmark.py -v --benchmark-only
    или
    python -m pytest tests/benchmark.py --benchmark-only
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.typos_dataset import get_benchmark_queries
from src.spell_checker import RussianSpellChecker


checker = None


def _get_checker():
    global checker
    if checker is None:
        checker = RussianSpellChecker(
            use_gpu=False,
            fallback_to_dict=True,
            config_path="config.yaml",
            model_mode="fast",
        )
    return checker


@pytest.fixture(scope="module")
def checker_fixture():
    return _get_checker()


@pytest.fixture(scope="module")
def texts():
    return get_benchmark_queries() * 10  # 100 запросов


def bench_dict_correction_only(benchmark):
    """Бенчмарк чисто словарного fallback (без модели)."""
    c = _get_checker()
    # Временно отключаем модель, чтобы бенчмарк был чистым
    c.corrector = None
    texts_local = get_benchmark_queries() * 10
    benchmark(c.correct_batch, texts_local)


def bench_full_pipeline_single(benchmark):
    """Бенчмарк полного pipeline для одного запроса (с кэшем очищенным)."""
    c = _get_checker()
    query = "тлефон"
    c.clear_cache()
    benchmark(c.correct, query)


def bench_full_pipeline_batch(benchmark):
    """Бенчмарк полного pipeline пакетно."""
    c = _get_checker()
    texts_local = get_benchmark_queries() * 10
    c.clear_cache()
    benchmark(c.correct_batch, texts_local)


def bench_ngram_index(benchmark):
    """Бенчмарк n-gram поиска кандидатов."""
    c = _get_checker()
    word = "тлефон"
    benchmark(c._candidates_by_ngram, word, 20)


def bench_phonetic_index(benchmark):
    """Бенчмарк фонетического поиска кандидатов."""
    c = _get_checker()
    word = "тилефон"
    benchmark(c._candidates_by_phonetic, word)


def bench_weighted_levenshtein(benchmark):
    """Бенчмарк взвешенного Левенштейна."""
    c = _get_checker()
    w1, w2 = "компютер", "компьютер"
    benchmark(c._score_candidate, w1, w2)
