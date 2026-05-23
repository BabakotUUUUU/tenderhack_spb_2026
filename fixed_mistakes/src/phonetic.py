"""
Фонетические алгоритмы для русского языка.
Реализует Soundex, Metaphone и комбинированный подход.
"""

import re
from typing import Dict, List, Set


# Маппинг букв на фонетические коды для Soundex-алгоритма (русский)
RUSSIAN_SOUNDEX_CODES: Dict[str, str] = {
    'б': '1', 'п': '1', 'ф': '1', 'в': '1',
    'ц': '2', 'с': '2', 'з': '2', 'с': '2',
    'д': '3', 'т': '3',
    'л': '4',
    'м': '5', 'н': '5',
    'р': '6',
    'г': '7', 'к': '7', 'х': '7',
    'ж': '8', 'ш': '8', 'ч': '8', 'щ': '8',
}

# Гласные (не кодируются)
RUSSIAN_VOWELS = {'а', 'е', 'ё', 'и', 'о', 'у', 'ы', 'э', 'ю', 'я', 'ь', 'ъ'}


def soundex_ru(word: str, length: int = 4) -> str:
    """
    Русская адаптация Soundex.
    Преобразует слово в фонетический код фиксированной длины.
    """
    word = word.lower().strip()
    if not word:
        return "0000"

    # Первая буква сохраняется
    first = word[0]
    result = [first]

    prev_code = RUSSIAN_SOUNDEX_CODES.get(first, '0')

    for char in word[1:]:
        if char in RUSSIAN_VOWELS:
            continue
        code = RUSSIAN_SOUNDEX_CODES.get(char, '0')
        if code != '0' and code != prev_code:
            result.append(code)
            prev_code = code
        if len(result) >= length:
            break

    # Дополняем нулями
    while len(result) < length:
        result.append('0')

    return ''.join(result[:length])


def _collapse(word: str, mapping: List[tuple]) -> str:
    """Вспомогательная функция для свёртки похожих звуков."""
    w = word.lower()
    for pattern, repl in mapping:
        w = re.sub(pattern, repl, w)
    return w


def metaphone_ru(word: str) -> str:
    """
    Упрощённая версия Metaphone для русского языка.
    Коллапсирует похожие звуки в один код.
    """
    w = word.lower().strip()
    if not w:
        return ""

    # Предварительные замены
    replacements = [
        (r'[ъь]', ''),          # твёрдый/мягкий знак
        (r'йо', 'ё'),
        (r'[ыи]', 'и'),
        (r'[оёэ]', 'о'),
        (r'[аея]', 'а'),
        (r'[ую]', 'у'),
        (r'[шщ]', 'ш'),
        (r'[жз]', 'ж'),
        (r'[чц]', 'ц'),
        (r'[бп]', 'б'),
        (r'[вф]', 'в'),
        (r'[гкх]', 'г'),
        (r'[дт]', 'д'),
        (r'[мн]', 'м'),
        (r'[рл]', 'р'),
        (r'[сз]', 'с'),
    ]

    w = _collapse(w, replacements)
    # Убираем дублирующиеся подряд идущие согласные
    w = re.sub(r'(.)\1+', r'\1', w)
    return w


def phonetic_key(word: str, algorithm: str = "combined") -> str:
    """
    Возвращает фонетический ключ слова.

    Args:
        word: исходное слово
        algorithm: 'soundex', 'metaphone', 'combined'
    """
    if algorithm == "soundex":
        return soundex_ru(word)
    if algorithm == "metaphone":
        return metaphone_ru(word)
    if algorithm == "combined":
        return soundex_ru(word) + "-" + metaphone_ru(word)
    raise ValueError(f"Неизвестный алгоритм: {algorithm}")


def phonetic_similarity(w1: str, w2: str, algorithm: str = "combined") -> float:
    """
    Вычисляет фонетическое сходство двух слов (0..1).
    """
    k1 = phonetic_key(w1, algorithm)
    k2 = phonetic_key(w2, algorithm)
    if k1 == k2:
        return 1.0

    # Нормализованное расстояние Левенштейна между фонетическими ключами
    try:
        import Levenshtein
        dist = Levenshtein.distance(k1, k2)
    except ImportError:
        # Fallback на ручную реализацию
        dist = _levenshtein_simple(k1, k2)
    max_len = max(len(k1), len(k2), 1)
    return 1.0 - dist / max_len


def _levenshtein_simple(s1: str, s2: str) -> int:
    """Простое рекурсивно-итеративное расстояние Левенштейна."""
    if len(s1) < len(s2):
        return _levenshtein_simple(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            ins = prev[j + 1] + 1
            dels = curr[j] + 1
            subs = prev[j] + (c1 != c2)
            curr.append(min(ins, dels, subs))
        prev = curr
    return prev[-1]


def build_phonetic_index(words: List[str], algorithm: str = "combined") -> Dict[str, Set[str]]:
    """
    Строит фонетический индекс: ключ -> множество слов.
    """
    index: Dict[str, Set[str]] = {}
    for w in words:
        key = phonetic_key(w, algorithm)
        index.setdefault(key, set()).add(w)
    return index
