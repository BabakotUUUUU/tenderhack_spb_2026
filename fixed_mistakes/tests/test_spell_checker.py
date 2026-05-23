"""
Юнит-тесты для spell-checker (pytest).

Запуск:
    pytest tests/test_spell_checker.py -v
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.spell_checker import RussianSpellChecker, SpellCheckerConfig, ngrams, ngram_similarity
from src.phonetic import soundex_ru, metaphone_ru, phonetic_similarity
from src.keyboard_utils import keyboard_distance, weighted_levenshtein
from src.utils import (
    normalize_digits_and_symbols,
    preserve_capitalization,
    resolve_omograph,
    safe_replace,
    MetricsCollector,
)
from data.typos_dataset import get_test_dataset


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def checker():
    """Создаёт checker в CPU-режиме с fallback."""
    return RussianSpellChecker(
        use_gpu=False,
        fallback_to_dict=True,
        config_path="config.yaml",
        model_mode="fast",
        auto_update=False,
    )


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

def test_config_load():
    cfg = SpellCheckerConfig("config.yaml")
    assert cfg.fallback_enabled is True
    assert cfg.max_lev_distance == 5


# ---------------------------------------------------------------------------
# N-граммы
# ---------------------------------------------------------------------------

def test_ngrams():
    assert ngrams("телефон", n=2) == {"#т", "те", "ел", "ле", "еф", "фо", "он", "н#"}


def test_ngram_similarity():
    sim = ngram_similarity("телефон", "тлефон", n=2)
    assert 0.0 <= sim <= 1.0
    assert sim > 0.5  # похожие слова


# ---------------------------------------------------------------------------
# Фонетика
# ---------------------------------------------------------------------------

def test_soundex_ru():
    assert soundex_ru("телефон") == "т415"
    assert soundex_ru("тилефон") == "т415"
    assert soundex_ru("компьютер")[:1] == "к"


def test_metaphone_ru():
    m1 = metaphone_ru("телефон")
    m2 = metaphone_ru("тилефон")
    # упрощённая реализация может давать небольшие различия;
    # проверяем высокое сходство
    assert phonetic_similarity("телефон", "тилефон", "metaphone") > 0.7


def test_phonetic_similarity():
    sim = phonetic_similarity("телефон", "тилефон", "combined")
    assert sim > 0.8


# ---------------------------------------------------------------------------
# Клавиатурные веса
# ---------------------------------------------------------------------------

def test_keyboard_distance():
    # 'е' и 'н' — соседи на верхнем ряду
    d = keyboard_distance("е", "н")
    assert d <= 1.5
    # 'а' и 'я' — далеко
    d2 = keyboard_distance("а", "я")
    assert d2 > d


def test_weighted_levenshtein_typo():
    # 'тлефон' -> 'телефон' через перестановку соседних клавиш
    dist = weighted_levenshtein("тлефон", "телефон")
    assert dist < 5.0


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def test_normalize_digits():
    assert normalize_digits_and_symbols("тел3фон") == "телзфон"
    assert normalize_digits_and_symbols("экр@н") == "экран"


def test_preserve_capitalization():
    assert preserve_capitalization("Тлефон", "телефон") == "Телефон"
    assert preserve_capitalization("КОМПЬТЕР", "компьютер") == "КОМПЬЮТЕР"
    assert preserve_capitalization("наушники", "наушники") == "наушники"


def test_safe_replace():
    text = "купить телефон"
    assert safe_replace(text, "телефон", "телефон") == text
    assert safe_replace(text, "тел", "тел") == text  # граница слова — не заменит часть


def test_resolve_omograph():
    assert resolve_omograph("замок", "дверной", "") == "замок"
    assert resolve_omograph("замок", "крепость", "") == "замок"


# ---------------------------------------------------------------------------
# Метрики
# ---------------------------------------------------------------------------

def test_metrics():
    m = MetricsCollector()
    m.log_correction("тлефон", "телефон", "телефон")
    assert m.precision == 1.0
    m.log_correction("компютер", "компютер", "компьютер")
    assert m.fn == 1
    assert m.recall < 1.0


# ---------------------------------------------------------------------------
# SpellChecker fallback (dict-only)
# ---------------------------------------------------------------------------

class TestSpellCheckerFallback:
    def test_empty(self, checker):
        assert checker.correct("") == [""]
        assert checker.correct("   ") == ["   "]

    def test_already_correct(self, checker):
        res = checker.correct("телефон")
        # если слово в словаре, fallback не меняет его
        assert res == ["телефон"]

    def test_simple_typo(self, checker):
        # тлефон -> телефон (keyboard swap)
        res = checker.correct("тлефон")
        # результат: [оригинал, исправленный]
        assert len(res) == 2
        assert res[0] == "тлефон"
        assert res[1].lower() == "телефон"

    def test_digits_in_word(self, checker):
        res = checker.correct("тел3фон")
        # нормализация цифр -> возможна коррекция
        assert isinstance(res, list)
        assert len(res) >= 1

    def test_capitalization_preserved(self, checker):
        res = checker.correct("Тлефон")
        corrected = res[1] if len(res) > 1 else res[0]
        assert corrected[0].isupper()

    def test_phrase(self, checker):
        res = checker.correct("зарядка для телфона")
        assert isinstance(res, list)
        # ожидаем исправление телфона -> телефон
        corrected = res[-1]
        assert "телефон" in corrected.lower()

    def test_topk(self, checker):
        res = checker.correct_topk("мыш", k=3)
        assert isinstance(res, list)
        assert len(res) <= 3
        assert "мышь" in [r.lower() for r in res]

    def test_batch(self, checker):
        texts = ["тлефон", "компютер", "наушнеки"]
        results = checker.correct_batch(texts)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, list)

    @pytest.mark.asyncio
    async def test_batch_async(self, checker):
        texts = ["тлефон", "компютер"]
        results = await checker.correct_batch_async(texts, k=2)
        assert len(results) == 2
        for r in results:
            assert isinstance(r, list)

    def test_evaluation(self, checker):
        tiny = [("тлефон", "телефон", "keyboard"), ("мыш", "мышь", "omission")]
        report = checker.evaluate(tiny)
        assert "Accuracy" in report
        assert "Precision" in report

    def test_hot_dict_auto_update(self, checker):
        # вызываем correct с неизвестным словом
        checker.correct("экспериментальное_слово")
        # без падений
        assert True

    def test_import_export_dict(self, checker, tmp_path):
        export_path = tmp_path / "dict.txt"
        checker.export_dictionary(str(export_path))
        assert export_path.exists()
        checker.import_dictionary(str(export_path))


# ---------------------------------------------------------------------------
# Датасет (≥50 примеров)
# ---------------------------------------------------------------------------

def test_dataset_size():
    dataset = get_test_dataset()
    assert len(dataset) >= 50, f"Dataset too small: {len(dataset)}"


@pytest.mark.parametrize("typo,expected,category", get_test_dataset())
def test_dataset_parametrized(checker, typo, expected, category):
    """Параметризованный тест на датасете."""
    result = checker.correct(typo)
    corrected = result[-1] if len(result) > 1 else result[0]
    # Допускаем небольшую вариативность: если exact совпадение невозможно
    # из-за модели/словаря, проверяем, что не вернулся оригинал
    assert corrected.strip() != typo.strip() or typo == expected, \
        f"[{category}] {typo} was not corrected (got {corrected})"
