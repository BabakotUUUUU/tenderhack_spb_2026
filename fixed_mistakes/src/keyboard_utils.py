"""
Утилиты для работы с клавиатурными ошибками.
Реализует взвешенное расстояние с учётом расположения клавиш на русской раскладке.
"""

from typing import Dict, Tuple, Optional
import math


# Русская стандартная раскладка (YCUKEN)
RUSSIAN_KEYBOARD_LAYOUT = [
    ['ё', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '='],
    ['й', 'ц', 'у', 'к', 'е', 'н', 'г', 'ш', 'щ', 'з', 'х', 'ъ', '\\'],
    ['ф', 'ы', 'в', 'а', 'п', 'р', 'о', 'л', 'д', 'ж', 'э'],
    ['я', 'ч', 'с', 'м', 'и', 'т', 'ь', 'б', 'ю', '.'],
]

# Альтернативная раскладка (для цифр на верхнем ряду с shift)
RUSSIAN_SHIFT_SYMBOLS = {
    '!': '1', '"': '2', '№': '3', ';': '4', '%': '5',
    ':': '6', '?': '7', '*': '8', '(': '9', ')': '0',
    '_': '-', '+': '=',
}


def _build_position_map(layout: list) -> Dict[str, Tuple[int, int]]:
    """Строит карту позиций символов на клавиатуре."""
    pos_map = {}
    for row_idx, row in enumerate(layout):
        for col_idx, char in enumerate(row):
            pos_map[char] = (row_idx, col_idx)
            pos_map[char.upper()] = (row_idx, col_idx)
    # Добавляем смещённые символы
    pos_map[','] = (3, 9)
    pos_map['/'] = (3, 10)
    return pos_map


_KEYBOARD_POS = _build_position_map(RUSSIAN_KEYBOARD_LAYOUT)


def normalize_char(char: str) -> str:
    """Приводит символ к нижнему регистру и раскрывает shift-символы."""
    if not char:
        return char
    c = char.lower()
    return RUSSIAN_SHIFT_SYMBOLS.get(c, c)


def keyboard_distance(c1: str, c2: str) -> float:
    """
    Возвращает "клавиатурное расстояние" между двумя символами.
    Чем ближе клавиши, тем меньше значение.
    """
    n1 = normalize_char(c1)
    n2 = normalize_char(c2)
    if n1 == n2:
        return 0.0

    p1 = _KEYBOARD_POS.get(n1)
    p2 = _KEYBOARD_POS.get(n2)

    if p1 is None or p2 is None:
        # Один из символов не на стандартной раскладке (например, латиница/цифра/спецсимвол)
        return 5.0

    row_diff = abs(p1[0] - p2[0])
    col_diff = abs(p1[1] - p2[1])

    # Соседние клавиши (включая диагональ)
    if row_diff <= 1 and col_diff <= 1 and (row_diff + col_diff) > 0:
        return 1.0
    # Одна рука, одна строка
    if row_diff == 0:
        return 1.5 + col_diff * 0.5
    # Разные руки / разные ряды
    return 2.0 + row_diff + col_diff * 0.5


def weighted_substitution_cost(c1: str, c2: str,
                                weight_adjacent: float = 1.0,
                                weight_same_row: float = 1.5,
                                weight_other: float = 3.0) -> float:
    """
    Возвращает вес замены символа с учётом клавиатурного расстояния.
    """
    if c1 == c2:
        return 0.0

    dist = keyboard_distance(c1, c2)
    if dist <= 1.0:
        return weight_adjacent
    if dist <= 2.0:
        return weight_same_row
    return weight_other


def weighted_levenshtein(s1: str, s2: str,
                         weight_adjacent: float = 1.0,
                         weight_same_row: float = 1.5,
                         weight_other: float = 3.0,
                         insertion_cost: float = 2.0,
                         deletion_cost: float = 2.0) -> float:
    """
    Взвешенное расстояние Левенштейна с клавиатурными весами.
    """
    if len(s1) < len(s2):
        return weighted_levenshtein(s2, s1, weight_adjacent, weight_same_row,
                                     weight_other, insertion_cost, deletion_cost)

    if len(s2) == 0:
        return len(s1) * deletion_cost

    previous_row = [j * insertion_cost for j in range(len(s2) + 1)]

    for i, c1 in enumerate(s1):
        current_row = [(i + 1) * deletion_cost]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + insertion_cost
            deletions = current_row[j] + deletion_cost
            substitutions = previous_row[j] + weighted_substitution_cost(
                c1, c2, weight_adjacent, weight_same_row, weight_other
            )
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def is_typo_by_keyboard(word: str, candidate: str, threshold: float = 4.0) -> bool:
    """
    Проверяет, может ли слово быть опечаткой кандидата с учётом клавиатурных весов.
    """
    dist = weighted_levenshtein(word.lower(), candidate.lower())
    return dist <= threshold
