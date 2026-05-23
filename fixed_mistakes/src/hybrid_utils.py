"""
Гибридные утилиты: объединение PriceHunt NLP + BART Spell Checker.

PriceHunt (пользовательский код):
  - pymorphy3 лемматизация
  - SYNONYM_MAP / TYPO_MAP
  - EN->RU keyboard layout fix
  - tire pattern detection
  - rapidfuzz fuzzy matching

BART Spell Checker (мой код):
  - N-gram индекс
  - Phonetic (Soundex/Metaphone)
  - Weighted Levenshtein с клавиатурными весами
  - Hot reload dictionary
  - Metrics collector
  - Capitalization preservation
"""

from __future__ import annotations

import os
import re
import json
import threading
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set, Any

# ---------------------------------------------------------------------------
# PriceHunt: Синонимы и опечатки (из query_processor.py)
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
    "игровой ноутбук": ["gaming laptop", "ноутбук для игр", "геймерский ноутбук"],
    "usb-накопитель": ["флешка", "usb флеш накопитель", "флэшка"],
    "шины r16": ["шины 205/55 r16", "резина r16", "16 радиус"],
    "шины r17": ["шины 225/45 r17", "резина r17", "17 радиус"],
    "шины r18": ["шины 235/45 r18", "резина r18", "18 радиус"],
    "картридж": ["toner", "тонер", "чернила для принтера", "расходник"],
}

# Обратный индекс: синоним -> канонический
SYNONYM_REVERSE: dict[str, str] = {}
for _canonical, _syns in SYNONYM_MAP.items():
    for _s in _syns:
        SYNONYM_REVERSE[_s.lower()] = _canonical

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
    "беспроводная мышь": "мышь беспроводная",
    "беспроводная клавиатура": "клавиатура беспроводная",
    "веб камера": "веб-камера",
    "вебкамера": "веб-камера",
    "ноутбук игровой": "игровой ноутбук",
    "зимняя резина": "зимние шины",
    "летняя резина": "летние шины",
    "мышка": "мышь",
    "флешка": "usb-накопитель",
    # Дополнительные из нашего датасета
    "тлефон": "телефон",
    "ркефон": "телефон",
    "текефон": "телефон",
    "компютер": "компьютер",
    "компьтер": "компьютер",
    "мыш": "мышь",
    "мыща": "мышь",
    "наушнеки": "наушники",
    "наущники": "наушники",
    "наушнтки": "наушники",
    "неотбук": "ноутбук",
    "клаиатура": "клавиатура",
    "клавеатура": "клавиатура",
    "экарн": "экран",
    "экпан": "экран",
    "зарядкп": "зарядка",
    "зарядвка": "зарядка",
    "планшит": "планшет",
    "планшкт": "планшет",
    "смартфоь": "смартфон",
    "смартфин": "смартфон",
    "принтр": "принтер",
    "принтет": "принтер",
    "сканерр": "сканер",
    "сквнер": "сканер",
    "роутерр": "роутер",
    "роуткр": "роутер",
    "модеь": "модем",
    "моден": "модем",
    "колонкп": "колонки",
    "колонви": "колонки",
    "микрофонн": "микрофон",
    "микрофоь": "микрофон",
    "вебкамерп": "вебкамера",
    "мониторр": "монитор",
    "мониток": "монитор",
    "монитр": "монитор",
    "тилефон": "телефон",
    "кампьютер": "компьютер",
    "кампютер": "компьютер",
    "наушници": "наушники",
    "нотбук": "ноутбук",
    "клавиатюра": "клавиатура",
    "телефоан": "телефон",
    "компьютре": "компьютер",
    "экранр": "экран",
    "роутре": "роутер",
}

TIRE_PATTERN = re.compile(r"\b(\d{3})[/\\](\d{2})\s*[rRрР](\d{2})\b")

_EN_TO_RU_LAYOUT = str.maketrans(
    "qwertyuiop[]asdfghjkl;'zxcvbnm,./`",
    "йцукенгшщзхъфывапролджэячсмитьбю.ё",
)


# ---------------------------------------------------------------------------
# PriceHunt: Лемматизация
# ---------------------------------------------------------------------------

_morph = None


def _get_morph():
    global _morph
    if _morph is None:
        try:
            import pymorphy3
            _morph = pymorphy3.MorphAnalyzer()
        except ImportError:
            pass
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


# ---------------------------------------------------------------------------
# PriceHunt: Исправление раскладки
# ---------------------------------------------------------------------------

KNOWN_RU_ROOTS = (
    "ноут", "принтер", "монитор", "клавиат", "мыш", "шин", "резин",
    "куртк", "кроссов", "ботин", "футбол", "картридж", "роутер",
    "телефон", "компьютер", "планшет", "смартфон", "наушник", "колонк",
    "микрофон", "сканер", "ксерокс", "проектор", "вебкамер", "экран",
    "клавиатур", "зарядк", "модем",
)


def fix_keyboard_layout(text: str) -> str:
    """Исправляет простую ошибку раскладки, не трогая латинские бренды."""
    stripped = text.strip()
    if stripped and re.fullmatch(r"[A-Za-z\[\];',./\`\s-]+", stripped):
        converted = stripped.lower().translate(_EN_TO_RU_LAYOUT)
        if any(root in converted for root in KNOWN_RU_ROOTS):
            return converted
    return text


# ---------------------------------------------------------------------------
# PriceHunt: Шины
# ---------------------------------------------------------------------------

def fix_tire_pattern(text: str) -> str:
    """Нормализует размер шин к формату XXX/XX RXX."""
    if TIRE_PATTERN.search(text):
        text = TIRE_PATTERN.sub(r"\1/\2 R\3", text)
        if "шин" not in text and "резин" not in text:
            text = "шины " + text
    return text


# ---------------------------------------------------------------------------
# BART: Weighted Levenshtein + Keyboard
# ---------------------------------------------------------------------------

RUSSIAN_KEYBOARD_LAYOUT = [
    ['ё', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '='],
    ['й', 'ц', 'у', 'к', 'е', 'н', 'г', 'ш', 'щ', 'з', 'х', 'ъ', '\\'],
    ['ф', 'ы', 'в', 'а', 'п', 'р', 'о', 'л', 'д', 'ж', 'э'],
    ['я', 'ч', 'с', 'м', 'и', 'т', 'ь', 'б', 'ю', '.'],
]

RUSSIAN_SHIFT_SYMBOLS = {
    '!': '1', '"': '2', '№': '3', ';': '4', '%': '5',
    ':': '6', '?': '7', '*': '8', '(': '9', ')': '0',
    '_': '-', '+': '=',
}


def _build_position_map(layout: list) -> dict:
    pos_map = {}
    for row_idx, row in enumerate(layout):
        for col_idx, char in enumerate(row):
            pos_map[char] = (row_idx, col_idx)
            pos_map[char.upper()] = (row_idx, col_idx)
    pos_map[','] = (3, 9)
    pos_map['/'] = (3, 10)
    return pos_map


_KEYBOARD_POS = _build_position_map(RUSSIAN_KEYBOARD_LAYOUT)


def normalize_char(ch: str) -> str:
    c = ch.lower()
    return RUSSIAN_SHIFT_SYMBOLS.get(c, c)


def keyboard_distance(c1: str, c2: str) -> float:
    n1 = normalize_char(c1)
    n2 = normalize_char(c2)
    if n1 == n2:
        return 0.0
    p1 = _KEYBOARD_POS.get(n1)
    p2 = _KEYBOARD_POS.get(n2)
    if p1 is None or p2 is None:
        return 5.0
    row_diff = abs(p1[0] - p2[0])
    col_diff = abs(p1[1] - p2[1])
    if row_diff <= 1 and col_diff <= 1 and (row_diff + col_diff) > 0:
        return 1.0
    if row_diff == 0:
        return 1.5 + col_diff * 0.5
    return 2.0 + row_diff + col_diff * 0.5


def weighted_substitution_cost(
    c1: str, c2: str,
    weight_adjacent: float = 1.0,
    weight_same_row: float = 1.5,
    weight_other: float = 3.0,
) -> float:
    if c1 == c2:
        return 0.0
    dist = keyboard_distance(c1, c2)
    if dist <= 1.0:
        return weight_adjacent
    if dist <= 2.0:
        return weight_same_row
    return weight_other


def weighted_levenshtein(
    s1: str, s2: str,
    weight_adjacent: float = 1.0,
    weight_same_row: float = 1.5,
    weight_other: float = 3.0,
    insertion_cost: float = 2.0,
    deletion_cost: float = 2.0,
) -> float:
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


# ---------------------------------------------------------------------------
# BART: Phonetic
# ---------------------------------------------------------------------------

RUSSIAN_SOUNDEX_CODES: Dict[str, str] = {
    'б': '1', 'п': '1', 'ф': '1', 'в': '1',
    'ц': '2', 'с': '2', 'з': '2',
    'д': '3', 'т': '3',
    'л': '4',
    'м': '5', 'н': '5',
    'р': '6',
    'г': '7', 'к': '7', 'х': '7',
    'ж': '8', 'ш': '8', 'ч': '8', 'щ': '8',
}

RUSSIAN_VOWELS = {'а', 'е', 'ё', 'и', 'о', 'у', 'ы', 'э', 'ю', 'я', 'ь', 'ъ'}


def soundex_ru(word: str, length: int = 4) -> str:
    word = word.lower().strip()
    if not word:
        return "0000"
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
    while len(result) < length:
        result.append('0')
    return ''.join(result[:length])


def metaphone_ru(word: str) -> str:
    w = word.lower().strip()
    if not w:
        return ""
    replacements = [
        (r'[ъь]', ''),          (r'йо', 'ё'),          (r'[ыи]', 'и'),
        (r'[оёэ]', 'о'),        (r'[аея]', 'а'),        (r'[ую]', 'у'),
        (r'[шщ]', 'ш'),         (r'[жз]', 'ж'),         (r'[чц]', 'ц'),
        (r'[бп]', 'б'),         (r'[вф]', 'в'),         (r'[гкх]', 'г'),
        (r'[дт]', 'д'),         (r'[мн]', 'м'),         (r'[рл]', 'р'),
        (r'[сз]', 'с'),
    ]
    for pattern, repl in replacements:
        w = re.sub(pattern, repl, w)
    w = re.sub(r'(.)\1+', r'\1', w)
    return w


def phonetic_key(word: str, algorithm: str = "combined") -> str:
    if algorithm == "soundex":
        return soundex_ru(word)
    if algorithm == "metaphone":
        return metaphone_ru(word)
    if algorithm == "combined":
        return soundex_ru(word) + "-" + metaphone_ru(word)
    raise ValueError(f"Неизвестный алгоритм: {algorithm}")


def phonetic_similarity(w1: str, w2: str, algorithm: str = "combined") -> float:
    k1 = phonetic_key(w1, algorithm)
    k2 = phonetic_key(w2, algorithm)
    if k1 == k2:
        return 1.0
    try:
        import Levenshtein
        dist = Levenshtein.distance(k1, k2)
    except ImportError:
        dist = _levenshtein_simple(k1, k2)
    max_len = max(len(k1), len(k2), 1)
    return 1.0 - dist / max_len


def _levenshtein_simple(s1: str, s2: str) -> int:
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
    index: Dict[str, Set[str]] = {}
    for w in words:
        key = phonetic_key(w, algorithm)
        index.setdefault(key, set()).add(w)
    return index


# ---------------------------------------------------------------------------
# BART: N-граммы
# ---------------------------------------------------------------------------

def ngrams(word: str, n: int = 2) -> Set[str]:
    w = f"#{word.lower()}#"
    return {w[i:i + n] for i in range(len(w) - n + 1)}


def ngram_similarity(w1: str, w2: str, n: int = 2) -> float:
    a = ngrams(w1, n)
    b = ngrams(w2, n)
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ---------------------------------------------------------------------------
# BART: Регистр, замены, омографы
# ---------------------------------------------------------------------------

RU_WORD_PATTERN = re.compile(r"[\u0400-\u04FF]+[\w\d@#$%&*]*[\u0400-\u04FF]+|[\u0400-\u04FF]+")


def normalize_digits_and_symbols(word: str) -> str:
    replacements = {
        '0': 'о', '3': 'з', '4': 'ч', '5': 'с', '6': 'б', '8': 'в',
        '1': 'л', '7': 'т',
        '@': 'а', '$': 'с', '#': 'н', '%': 'о', '&': 'и', '*': 'о',
    }
    return "".join(replacements.get(ch, ch) for ch in word.lower())


def preserve_capitalization(original: str, corrected: str) -> str:
    if original.isupper():
        return corrected.upper()
    if original and original[0].isupper():
        return corrected[0].upper() + corrected[1:].lower()
    return corrected.lower()


def extract_words(text: str) -> List[Tuple[str, int, int]]:
    return [(m.group(), m.start(), m.end()) for m in RU_WORD_PATTERN.finditer(text)]


def safe_replace(text: str, old: str, new: str) -> str:
    pattern = re.compile(r'\b' + re.escape(old) + r'\b', re.IGNORECASE)
    return pattern.sub(new, text)


OMOGRAPHS: Dict[str, List[Tuple[Optional[str], str]]] = {
    "ключ": [("дверной", "ключ"), ("реки", "ключ")],
    "коса": [("травы", "коса"), ("девушки", "коса")],
    "замок": [("дверной", "замок"), ("крепость", "замок")],
    "мишка": [("медведь", "мишка"), ("глаз", "мишка")],
    "печь": [("дрова", "печь"), ("выпекать", "печь")],
    "рука": [("часов", "рука"), ("человека", "рука")],
}


def resolve_omograph(word: str, context_before: str = "", context_after: str = "") -> str:
    variants = OMOGRAPHS.get(word.lower())
    if not variants:
        return word
    context = (context_before + " " + context_after).lower()
    best = variants[0][1]
    best_score = -1
    for hint, corr in variants:
        if hint and hint in context:
            return corr
        score = sum(1 for h in hint.split() if h in context) if hint else 0
        if score > best_score:
            best_score = score
            best = corr
    return best


# ---------------------------------------------------------------------------
# BART: Словарь (hot reload)
# ---------------------------------------------------------------------------

def load_dictionary(path: str) -> Set[str]:
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
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        for w in sorted(words):
            f.write(w + '\n')


class HotReloadDictionary:
    def __init__(self, path: str, interval_sec: int = 300):
        self.path = Path(path)
        self.interval_sec = interval_sec
        self._words: Set[str] = set()
        self._last_mtime = 0.0
        self._last_check = 0.0
        self._lock = threading.RLock()
        self._reload_if_needed()

    def _reload_if_needed(self):
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


# ---------------------------------------------------------------------------
# BART: Метрики
# ---------------------------------------------------------------------------

class MetricsCollector:
    def __init__(self, metrics_file: Optional[str] = None):
        self.metrics_file = metrics_file
        self.total = 0
        self.tp = 0
        self.fp = 0
        self.fn = 0
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
