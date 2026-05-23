"""
Вспомогательные утилиты для работы со словарём, регистром, омографами и цифрами.
"""

import os
import re
import threading
import json
from typing import Dict, List, Tuple, Optional, Set
from pathlib import Path


# Регулярное выражение для извлечения русских слов с возможными цифрами/спецсимволами внутри
RU_WORD_PATTERN = re.compile(r"[\u0400-\u04FF]+[\w\d@#$%&*]*[\u0400-\u04FF]+|[\u0400-\u04FF]+")
# Отдельно слова, которые могут содержать латиницу/цифры (бренды)
MIXED_WORD_PATTERN = re.compile(r"[\u0400-\u04FF]*[a-zA-Z0-9]+[\u0400-\u04FF]*")


def normalize_digits_and_symbols(word: str) -> str:
    """
    Заменяет цифры и спецсимволы внутри русского слова на близкие по форме буквы
    или удаляет их для поиска по словарю.
    """
    # Маппинг похожих символов
    replacements = {
        '0': 'о', '3': 'з', '4': 'ч', '5': 'с', '6': 'б', '8': 'в',
        '1': 'л', '7': 'т',
        '@': 'а', '$': 'с', '#': 'н', '%': 'о', '&': 'и', '*': 'о',
    }
    result = []
    for ch in word.lower():
        result.append(replacements.get(ch, ch))
    return ''.join(result)


def preserve_capitalization(original: str, corrected: str) -> str:
    """
    Сохраняет оригинальную капитализацию при замене слова.

    Правила:
      - ВЕСЬ ВЕРХНИЙ -> ВЕСЬ ВЕРХНИЙ
      - Первая заглавная -> Первая заглавная
      - иначе -> нижний регистр
    """
    if original.isupper():
        return corrected.upper()
    if original and original[0].isupper():
        return corrected[0].upper() + corrected[1:].lower()
    return corrected.lower()


def extract_words(text: str) -> List[Tuple[str, int, int]]:
    """
    Извлекает слова из текста, возвращает (слово, start, end).
    """
    words = []
    for m in RU_WORD_PATTERN.finditer(text):
        words.append((m.group(), m.start(), m.end()))
    return words


def safe_replace(text: str, old: str, new: str) -> str:
    """
    Безопасная замена с учётом границ слова (через регулярное выражение).
    """
    pattern = re.compile(r'\b' + re.escape(old) + r'\b', re.IGNORECASE)
    return pattern.sub(new, text)


def load_dictionary(path: str) -> Set[str]:
    """Загружает словарь из файла (по одному слову на строку)."""
    words = set()
    if not os.path.exists(path):
        return words
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                w = line.strip().lower()
                if w:
                    words.add(w)
    except Exception as e:
        print(f"Ошибка загрузки словаря {path}: {e}")
    return words


def save_dictionary(words: Set[str], path: str) -> None:
    """Сохраняет словарь в файл."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        for w in sorted(words):
            f.write(w + '\n')


class HotReloadDictionary:
    """
    Словарь с возможностью горячей перезагрузки из файла.
    Проверяет mtime файла с заданным интервалом.
    """
    def __init__(self, path: str, interval_sec: int = 300):
        self.path = Path(path)
        self.interval_sec = interval_sec
        self._words: Set[str] = set()
        self._last_mtime = 0.0
        self._last_check = 0.0
        self._lock = threading.RLock()
        self._reload_if_needed()

    def _reload_if_needed(self):
        import time
        now = time.time()
        if now - self._last_check < self.interval_sec:
            return
        self._last_check = now
        if not self.path.exists():
            return
        mtime = self.path.stat().st_mtime
        if mtime != self._last_mtime:
            with self._lock:
                self._words = load_dictionary(str(self.path))
                self._last_mtime = mtime

    def get_words(self) -> Set[str]:
        self._reload_if_needed()
        with self._lock:
            return set(self._words)

    def contains(self, word: str) -> bool:
        self._reload_if_needed()
        with self._lock:
            return word.lower() in self._words

    def add(self, word: str) -> None:
        w = word.lower().strip()
        if not w:
            return
        with self._lock:
            if w not in self._words:
                self._words.add(w)
                save_dictionary(self._words, str(self.path))
                self._last_mtime = self.path.stat().st_mtime


class MetricsCollector:
    """
    Простой сборщик метрик качества исправлений.
    """
    def __init__(self, metrics_file: Optional[str] = None):
        self.metrics_file = metrics_file
        self.total = 0
        self.tp = 0  # true positives (исправили и правильно)
        self.fp = 0  # false positives (исправили, но зря)
        self.fn = 0  # false negatives (не исправили ошибку)
        self._lock = threading.Lock()

    def log_correction(self, original: str, corrected: str, expected: Optional[str] = None):
        with self._lock:
            self.total += 1
            if expected is not None:
                if corrected == expected and original != expected:
                    self.tp += 1
                elif corrected != original and corrected != expected:
                    self.fp += 1
                elif corrected == original and original != expected:
                    self.fn += 1
            if self.metrics_file:
                try:
                    with open(self.metrics_file, 'a', encoding='utf-8') as f:
                        rec = json.dumps({
                            "original": original,
                            "corrected": corrected,
                            "expected": expected,
                        }, ensure_ascii=False)
                        f.write(rec + '\n')
                except Exception:
                    pass

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p = self.precision
        r = self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def report(self) -> str:
        return (
            f"Metrics: total={self.total}, TP={self.tp}, FP={self.fp}, FN={self.fn}\n"
            f"Precision={self.precision:.3f}, Recall={self.recall:.3f}, F1={self.f1:.3f}"
        )


# Словарь омографов: ключ — омограф, значения — (POS_hint, correction)
# POS_hint может быть подсказкой из контекста (предыдущее/следующее слово)
OMOGRAPHS: Dict[str, List[Tuple[Optional[str], str]]] = {
    "ключ": [("дверной", "ключ"), ("реки", "ключ")],
    "коса": [("травы", "коса"), ("девушки", "коса")],
    "замок": [("дверной", "замок"), ("крепость", "замок")],
    "мишка": [("медведь", "мишка"), ("глаз", "мишка")],
    "печь": [("дрова", "печь"), ("выпекать", "печь")],
    "рука": [("часов", "рука"), ("человека", "рука")],
}


def resolve_omograph(word: str, context_before: str = "", context_after: str = "") -> str:
    """
    Простая эвристика для разрешения омографов по контексту.
    """
    variants = OMOGRAPHS.get(word.lower())
    if not variants:
        return word

    context = (context_before + " " + context_after).lower()
    best = variants[0][1]
    best_score = -1
    for hint, corr in variants:
        if hint and hint in context:
            return corr
        # Простой подсчёт похожести контекста
        score = sum(1 for h in hint.split() if h in context) if hint else 0
        if score > best_score:
            best_score = score
            best = corr
    return best
