"""
NLP модуль: нормализация запросов, исправление опечаток, синонимы.

Используется ИСКЛЮЧИТЕЛЬНО локальное/open-source ПО без внешних API:
  - pymorphy3    — морфологический анализатор русского языка (правила, не ML)
                   лемматизирует слова: «ноутбуки» → «ноутбук»
  - rapidfuzz    — нечёткое сравнение строк (расстояние Левенштейна)
                   исправляет опечатки: «ноутбукк» → «ноутбук»
  - Встроенный словарь синонимов для товарных категорий хакатона

Вес решения: pymorphy3 ~15 MB, rapidfuzz ~1 MB.
Время обработки одного запроса: < 5 мс.
"""

import logging
import re
from functools import lru_cache
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Морфологический анализатор (lazy init — не нагружает старт)
# ---------------------------------------------------------------------------
_morph = None


def _get_morph():
    global _morph
    if _morph is None:
        try:
            import pymorphy3
            _morph = pymorphy3.MorphAnalyzer()
            logger.info("[NLP] pymorphy3 loaded")
        except ImportError:
            logger.warning("[NLP] pymorphy3 not installed — lemmatization disabled")
    return _morph


@lru_cache(maxsize=4096)
def _lemmatize_word(word: str) -> str:
    """Возвращает начальную форму слова (лемму) через pymorphy3."""
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
    lemmas = [_lemmatize_word(w) for w in words]
    return " ".join(lemmas)


# ---------------------------------------------------------------------------
# Синонимы для товарных категорий хакатона
# ---------------------------------------------------------------------------
SYNONYM_MAP: dict[str, list[str]] = {
    # Одежда
    "футболка": ["t-shirt", "тишка", "тишерт", "майка", "поло"],
    "куртка": ["jacket", "ветровка", "пуховик", "парка", "анорак", "бомбер"],
    "брюки": ["штаны", "джинсы", "слаксы", "трузера", "чинос"],
    "платье": ["сарафан", "dress", "юбка"],
    "пальто": ["coat", "шуба", "дубленка", "полупальто"],
    "кроссовки": ["sneakers", "кеды", "тапки", "спортивная обувь", "кросовки"],
    "ботинки": ["boots", "туфли", "полуботинки", "сапоги"],
    "рубашка": ["shirt", "блуза", "блузка"],
    "свитер": ["джемпер", "пуловер", "свитшот", "толстовка", "худи"],
    # Шины
    "шины": ["резина", "покрышки", "tires", "tyres", "колёса"],
    "летние шины": ["летняя резина", "summer tires", "шины лето"],
    "зимние шины": ["зимняя резина", "winter tires", "шиповки", "нешипованные", "липучки"],
    "всесезонные шины": ["всесезонка", "all-season tires", "всесезон"],
    # Оргтехника
    "ноутбук": ["laptop", "лэптоп", "нетбук", "ультрабук", "macbook", "нотбук"],
    "принтер": ["printer", "лазерный принтер", "струйный принтер", "мфу принтер"],
    "мфу": ["многофункциональное устройство", "принтер сканер копир", "aio"],
    "сканер": ["scanner", "планшетный сканер"],
    "монитор": ["monitor", "дисплей", "экран", "моник"],
    "клавиатура": ["keyboard", "клава"],
    "мышь": ["мышка", "mouse", "грызун"],
    "проектор": ["projector", "мультимедийный проектор"],
    "ксерокс": ["копир", "копировальный аппарат", "xerox"],
    "компьютер": ["пк", "системный блок", "десктоп", "pc", "desktop", "моноблок"],
    "планшет": ["tablet", "ipad", "графический планшет"],
    "наушники": ["headphones", "гарнитура", "беспроводные наушники", "tws"],
    "веб-камера": ["webcam", "камера для компьютера"],
    "роутер": ["router", "маршрутизатор", "wifi роутер", "вай фай роутер"],
}

# Обратный индекс: синоним → канонический термин
_REVERSE: dict[str, str] = {}
for _canonical, _syns in SYNONYM_MAP.items():
    for _s in _syns:
        _REVERSE[_s.lower()] = _canonical

# ---------------------------------------------------------------------------
# Ручной словарь опечаток (быстрый O(1) lookup)
# ---------------------------------------------------------------------------
TYPO_MAP: dict[str, str] = {
    "ноутбукк": "ноутбук",
    "нотбук": "ноутбук",
    "лаптоп": "ноутбук",
    "принтар": "принтер",
    "монитар": "монитор",
    "клавиатурка": "клавиатура",
    "шинны": "шины",
    "куртак": "куртка",
    "ботники": "ботинки",
    "кросовки": "кроссовки",
    "кросовка": "кроссовки",
    "маус": "мышь",
    "моус": "мышь",
    "рутер": "роутер",
    "роутер": "роутер",
    "кавиатура": "клавиатура",
    "принер": "принтер",
    "сканнер": "сканер",
    "прожектор": "проектор",
}


# ---------------------------------------------------------------------------
# Основные функции
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def correct_query(query: str) -> Tuple[str, bool]:
    """
    Исправляет опечатки в запросе.
    Возвращает (исправленный, был_ли_исправлен).

    Алгоритм:
      1. Лемматизация через pymorphy3
      2. Прямой lookup в словаре опечаток
      3. Нечёткое сравнение через rapidfuzz (Левенштейн, порог 82%)
    """
    normalized = _normalize(query)

    # 1. Прямая проверка словаря
    if normalized in TYPO_MAP:
        return TYPO_MAP[normalized], True

    # 2. Лемматизируем и проверяем снова
    lemmatized = lemmatize_query(normalized)
    if lemmatized != normalized and lemmatized in TYPO_MAP:
        return TYPO_MAP[lemmatized], True

    # 3. rapidfuzz по словам
    try:
        from rapidfuzz import process, fuzz

        all_known = (
            list(TYPO_MAP.keys())
            + list(SYNONYM_MAP.keys())
            + [s for syns in SYNONYM_MAP.values() for s in syns]
        )

        words = normalized.split()
        corrected_words: list[str] = []
        was_corrected = False

        for word in words:
            if len(word) <= 3:
                corrected_words.append(word)
                continue

            lemma = _lemmatize_word(word)

            match = process.extractOne(
                lemma, all_known,
                scorer=fuzz.ratio,
                score_cutoff=82,
            )
            if match and match[0] != lemma:
                canonical = _REVERSE.get(match[0], match[0])
                corrected_words.append(canonical)
                was_corrected = True
            else:
                corrected_words.append(lemma if lemma != word else word)

        result = " ".join(corrected_words)
        return result, was_corrected

    except ImportError:
        logger.warning("[NLP] rapidfuzz not installed")
        return normalized, False


def expand_synonyms(query: str) -> list[str]:
    """
    Возвращает список вариантов запроса с учётом синонимов.
    Максимум 4 варианта, чтобы не спамить парсерами.
    """
    normalized = _normalize(query)
    variants: set[str] = {normalized}

    # Если запрос — синоним, добавляем канонический
    canonical = _REVERSE.get(normalized)
    if canonical:
        variants.add(canonical)

    # Если запрос — канонический, добавляем 2 синонима
    if normalized in SYNONYM_MAP:
        for syn in SYNONYM_MAP[normalized][:2]:
            variants.add(syn)

    # Лемматизированный вариант
    lemmatized = lemmatize_query(normalized)
    if lemmatized != normalized:
        variants.add(lemmatized)
        canonical2 = _REVERSE.get(lemmatized)
        if canonical2:
            variants.add(canonical2)

    return list(variants)[:4]


def process_query(raw_query: str) -> dict:
    """
    Полный пайплайн обработки поискового запроса.

    Возвращает:
      original        — исходный запрос
      corrected       — исправленный запрос
      was_corrected   — True если было исправление
      search_variants — список вариантов для поиска
      primary_query   — основной запрос для парсеров
    """
    corrected, was_corrected = correct_query(raw_query)
    variants = expand_synonyms(corrected)

    return {
        "original": raw_query,
        "corrected": corrected,
        "was_corrected": was_corrected,
        "search_variants": variants,
        "primary_query": corrected,
    }
