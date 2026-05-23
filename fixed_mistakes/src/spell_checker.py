"""
Исправление опечаток в русских поисковых запросах.

Улучшенная версия с:
  - кэшированием (functools.lru_cache)
  - асинхронной пакетной обработкой
  - поддержкой лёгкой модели (rubert-tiny2)
  - множественными исправлениями (топ-k)
  - контекстной коррекцией
  - обработкой цифр/спецсимволов
  - автоматическим обновлением словаря
  - фонетическими алгоритмами
  - n-граммами
  - клавиатурными весами
  - логированием и метриками
  - горячей перезагрузкой словаря
  - сохранением регистра
  - разрешением омографов

Дополнительные библиотеки (установить через pip):
    torch transformers sentencepiece accelerate
    pymorphy3 pymorphy3-dicts-ru
    pyyaml python-Levenshtein nltk
    structlog fastapi uvicorn httpx
    pytest pytest-asyncio pytest-benchmark

Ключевые изменения:
  1. Проблема замены частей слов в fallback исправлена через
     safe_replace с границами слова и preserve_capitalization.
  2. Добавлен LRU-кэш на correct() и пакетный async метод.
  3. Возможность переключения между primary / fast моделью.
  4. Словарь автоматически расширяется уникальными пользовательскими
     запросами (при включённом auto_update).
  5. Взвешенное Левенштейн-расстояние учитывает близость клавиш.
  6. N-граммный индекс и фонетический индекс ускоряют поиск кандидатов.
"""

from __future__ import annotations

import os
import re
import json
import asyncio
import threading
import hashlib
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Dict, Set, Tuple, Any

import yaml

# --- опциональные тяжёлые зависимости ---
try:
    import torch
    _HAS_TORCH = True
except ImportError:
    torch = None  # type: ignore
    _HAS_TORCH = False

try:
    from transformers import pipeline, AutoTokenizer, AutoModelForSeq2SeqLM
    _HAS_TRANSFORMERS = True
except ImportError:
    pipeline = AutoTokenizer = AutoModelForSeq2SeqLM = None  # type: ignore
    _HAS_TRANSFORMERS = False

# --- локальные модули ---
from src.keyboard_utils import (
    weighted_levenshtein,
    keyboard_distance,
)
from src.phonetic import (
    phonetic_key,
    phonetic_similarity,
    build_phonetic_index,
)
from src.utils import (
    normalize_digits_and_symbols,
    preserve_capitalization,
    extract_words,
    safe_replace,
    load_dictionary,
    save_dictionary,
    HotReloadDictionary,
    MetricsCollector,
    resolve_omograph,
)

# Optional structlog
try:
    import structlog
    _logger = structlog.get_logger()
except ImportError:
    import logging as _logging
    _logger = _logging.getLogger("spell_checker")


# ---------------------------------------------------------------------------
# N-граммы
# ---------------------------------------------------------------------------

def ngrams(word: str, n: int = 2) -> Set[str]:
    """Возвращает множество n-грамм (по умолчанию биграмм) для слова."""
    w = f"#{word.lower()}#"
    return {w[i:i + n] for i in range(len(w) - n + 1)}


def ngram_similarity(w1: str, w2: str, n: int = 2) -> float:
    """Jaccard similarity между n-граммами двух слов."""
    a = ngrams(w1, n)
    b = ngrams(w2, n)
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ---------------------------------------------------------------------------
# Кэширование
# ---------------------------------------------------------------------------

def _make_key(text: str, max_length: int, num_return: int, model_name: str) -> str:
    """Создаёт детерминированный ключ для кэша."""
    raw = f"{text.strip().lower()}|{max_length}|{num_return}|{model_name}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

class SpellCheckerConfig:
    """Загружает и хранит конфигурацию из YAML."""

    def __init__(self, path: str = "config.yaml"):
        self.path = path
        self._cfg: dict = self._load()

    def _load(self) -> dict:
        default = {
            "models": {
                "primary": {
                    "name": "ai-forever/FRED-T5-1.7B-spell-distilled-100m",
                    "use_gpu": True,
                    "max_length": 128,
                    "num_beams": 5,
                    "temperature": 1.0,
                },
                "fast": {
                    "name": "cointegrated/rubert-tiny2",
                    "use_gpu": False,
                    "max_length": 64,
                    "num_beams": 3,
                    "temperature": 1.0,
                },
            },
            "fallback": {
                "enabled": True,
                "dictionary_path": "data/common_words.txt",
                "auto_update": True,
                "max_levenshtein_distance": 2,
                "hot_reload_interval": 300,
            },
            "keyboard": {
                "weight_adjacent": 1.0,
                "weight_same_row": 1.5,
                "weight_other": 3.0,
            },
            "phonetic": {
                "enabled": True,
                "algorithm": "combined",
            },
            "cache": {"maxsize": 4096, "ttl": 3600},
            "logging": {
                "level": "INFO",
                "file": "logs/spell_corrections.log",
                "metrics_enabled": True,
                "metrics_file": "logs/metrics.jsonl",
            },
            "api": {
                "host": "0.0.0.0",
                "port": 8000,
                "workers": 1,
                "max_batch_size": 32,
            },
        }
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                if loaded:
                    default.update(loaded)
            except Exception as exc:
                _logger.warning("Не удалось загрузить конфиг, используются дефолты", error=str(exc))
        return default

    def get(self, *keys: str, default: Any = None) -> Any:
        d = self._cfg
        for k in keys:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                return default
        return d

    @property
    def model_primary(self) -> dict:
        return self.get("models", "primary", default={})

    @property
    def model_fast(self) -> dict:
        return self.get("models", "fast", default={})

    @property
    def fallback_enabled(self) -> bool:
        return bool(self.get("fallback", "enabled", default=True))

    @property
    def dict_path(self) -> str:
        return str(self.get("fallback", "dictionary_path", default="data/common_words.txt"))

    @property
    def auto_update_dict(self) -> bool:
        return bool(self.get("fallback", "auto_update", default=True))

    @property
    def max_lev_distance(self) -> int:
        return int(self.get("fallback", "max_levenshtein_distance", default=2))

    @property
    def hot_reload_interval(self) -> int:
        return int(self.get("fallback", "hot_reload_interval", default=300))

    @property
    def cache_maxsize(self) -> int:
        return int(self.get("cache", "maxsize", default=4096))

    @property
    def phonetic_enabled(self) -> bool:
        return bool(self.get("phonetic", "enabled", default=True))

    @property
    def phonetic_algorithm(self) -> str:
        return str(self.get("phonetic", "algorithm", default="combined"))

    @property
    def metrics_file(self) -> Optional[str]:
        return self.get("logging", "metrics_file", default="logs/metrics.jsonl")


# ---------------------------------------------------------------------------
# Основной класс
# ---------------------------------------------------------------------------

class RussianSpellChecker:
    """
    Исправление опечаток в русских поисковых запросах.

    Публичный интерфейс (обратная совместимость):
        checker = RussianSpellChecker(use_gpu=True, fallback_to_dict=True)
        results = checker.correct(text)          # -> List[str]

    Новые возможности:
        checker.correct_topk(text, k=3)          # -> List[str]
        checker.correct_batch(texts)             # -> List[List[str]]  (синхронно)
        await checker.correct_batch_async(texts) # -> List[List[str]]  (async)
        checker.switch_model("fast")             # переключить модель
    """

    def __init__(
        self,
        use_gpu: bool = True,
        fallback_to_dict: bool = True,
        config_path: str = "config.yaml",
        model_mode: str = "primary",
        auto_update: bool = True,
    ):
        """
        Args:
            use_gpu: использовать ли CUDA
            fallback_to_dict: включить ли словарный fallback
            config_path: путь к YAML-конфигу
            model_mode: "primary" (FRED-T5), "fast" (rubert-tiny2), "auto" (по возможности)
            auto_update: обновлять ли словарь новыми запросами
        """
        self.config = SpellCheckerConfig(config_path)
        self.fallback_to_dict = fallback_to_dict and self.config.fallback_enabled
        self.auto_update = auto_update and self.config.auto_update_dict
        self.model_mode = model_mode
        self._lock = threading.RLock()

        # --- модель ---
        self.corrector: Optional[Any] = None
        self.tokenizer: Optional[Any] = None
        self.model: Optional[Any] = None
        self.current_model_name: str = ""
        self.device: int = -1
        if _HAS_TORCH and use_gpu:
            self.device = 0 if torch.cuda.is_available() else -1  # type: ignore

        # --- словарь ---
        self._dict_path = self.config.dict_path
        os.makedirs(os.path.dirname(self._dict_path) or ".", exist_ok=True)
        if not os.path.exists(self._dict_path):
            _default = {
                "телефон", "компьютер", "компьютера", "мышь", "клавиатура",
                "экран", "наушники", "зарядка", "ноутбук", "планшет",
                "смартфон", "принтер", "сканер", "роутер", "модем",
                "колонки", "микрофон", "вебкамера", "монитор",
                "apple", "samsung", "nokia", "игр", "для", "дюйма",
                "беспроводная",
            }
            save_dictionary(_default, self._dict_path)

        self.hot_dict = HotReloadDictionary(
            self._dict_path,
            interval_sec=self.config.hot_reload_interval,
        )

        # --- индексы ---
        self._ngram_index: Dict[str, Set[str]] = defaultdict(set)
        self._phonetic_index: Dict[str, Set[str]] = {}
        self._rebuild_indices()

        # --- метрики ---
        self.metrics = MetricsCollector(self.config.metrics_file)

        # --- загрузка модели ---
        self._load_model()

    # -----------------------------------------------------------------------
    # Загрузка / переключение модели
    # -----------------------------------------------------------------------

    def _get_model_config(self) -> dict:
        if self.model_mode == "fast":
            return self.config.model_fast
        if self.model_mode == "auto":
            if self.device != -1 and _HAS_TORCH:
                try:
                    total_mem = torch.cuda.get_device_properties(0).total_memory  # type: ignore
                    if total_mem > 6 * 1024 ** 3:
                        return self.config.model_primary
                except Exception:
                    pass
            return self.config.model_fast
        return self.config.model_primary

    def switch_model(self, mode: str) -> None:
        """Переключает режим модели ('primary', 'fast', 'auto')."""
        with self._lock:
            if mode not in {"primary", "fast", "auto"}:
                raise ValueError(f"Неверный режим модели: {mode}")
            self.model_mode = mode
            self._cleanup_model()
            self._load_model()

    def _cleanup_model(self) -> None:
        """Освобождает память модели."""
        if self.model is not None:
            del self.model
            self.model = None
        if self.tokenizer is not None:
            del self.tokenizer
            self.tokenizer = None
        self.corrector = None
        if self.device != -1 and _HAS_TORCH:
            try:
                torch.cuda.empty_cache()  # type: ignore
            except Exception:
                pass

    def _load_model(self) -> None:
        """Загружает (или пытается загрузить) модель."""
        if not _HAS_TRANSFORMERS or not _HAS_TORCH:
            _logger.warning("transformers или torch не установлены — модель недоступна")
            self.corrector = None
            return

        cfg = self._get_model_config()
        self.current_model_name = cfg.get("name", "")
        try:
            _logger.info("Загрузка модели", model=self.current_model_name, device=self.device)
            self.tokenizer = AutoTokenizer.from_pretrained(self.current_model_name)
            self.model = AutoModelForSeq2SeqLM.from_pretrained(self.current_model_name)

            if self.device != -1:
                self.model = self.model.to("cuda")

            gen_kwargs = {
                "max_length": cfg.get("max_length", 128),
                "num_beams": cfg.get("num_beams", 5),
                "temperature": cfg.get("temperature", 1.0),
                "early_stopping": True,
            }

            self.corrector = pipeline(
                "text2text-generation",
                model=self.model,
                tokenizer=self.tokenizer,
                device=self.device,
                **gen_kwargs,
            )
            _logger.info("Модель успешно загружена")
        except Exception as exc:
            _logger.warning("Ошибка загрузки модели", error=str(exc))
            self.corrector = None
            if self.fallback_to_dict:
                _logger.info("Используется словарный fallback")

    # -----------------------------------------------------------------------
    # Индексы
    # -----------------------------------------------------------------------

    def _rebuild_indices(self) -> None:
        """Перестраивает n-граммный и фонетический индексы по текущему словарю."""
        words = self.hot_dict.get_words()
        self._ngram_index = defaultdict(set)
        for w in words:
            for ng in ngrams(w, n=2):
                self._ngram_index[ng].add(w)
        if self.config.phonetic_enabled:
            self._phonetic_index = build_phonetic_index(
                list(words), self.config.phonetic_algorithm
            )

    def _candidates_by_ngram(self, word: str, topn: int = 20) -> List[str]:
        """Возвращает кандидатов через пересечение n-грамм."""
        word_ngs = ngrams(word, n=2)
        counter: Dict[str, int] = defaultdict(int)
        for ng in word_ngs:
            for cand in self._ngram_index.get(ng, set()):
                counter[cand] += 1
        sorted_cands = sorted(counter.items(), key=lambda x: (-x[1], x[0]))
        return [c for c, _ in sorted_cands[:topn]]

    def _candidates_by_phonetic(self, word: str) -> List[str]:
        """Возвращает кандидатов с таким же фонетическим ключом."""
        if not self.config.phonetic_enabled:
            return []
        key = phonetic_key(word, self.config.phonetic_algorithm)
        return list(self._phonetic_index.get(key, set()))

    # -----------------------------------------------------------------------
    # Словарные методы
    # -----------------------------------------------------------------------

    def _update_dict_from_text(self, text: str) -> None:
        """Добавляет новые слова (длиннее 2 символов) в пользовательский словарь."""
        if not self.auto_update:
            return
        words = re.findall(r"[а-яё]+", text.lower())
        changed = False
        for w in words:
            if len(w) > 2:
                before = len(self.hot_dict.get_words())
                self.hot_dict.add(w)
                after = len(self.hot_dict.get_words())
                if after != before:
                    changed = True
        if changed:
            self._rebuild_indices()

    def _score_candidate(self, word: str, candidate: str) -> float:
        """
        Комбинированный скор кандидата: левенштейн + клавиатура + ngram + phonetic.
        Чем меньше — тем лучше.
        """
        w = word.lower()
        c = candidate.lower()

        lev = weighted_levenshtein(
            w, c,
            weight_adjacent=self.config.get("keyboard", "weight_adjacent", default=1.0),
            weight_same_row=self.config.get("keyboard", "weight_same_row", default=1.5),
            weight_other=self.config.get("keyboard", "weight_other", default=3.0),
        )
        ng_sim = ngram_similarity(w, c, n=2)
        ph_sim = phonetic_similarity(w, c, self.config.phonetic_algorithm)

        score = lev * 1.0 + (1.0 - ng_sim) * 2.0 + (1.0 - ph_sim) * 2.0
        return score

    def _dict_correction_topk(self, word: str, k: int = 3) -> List[Tuple[str, float]]:
        """Возвращает топ-k кандидатов из словаря."""
        if not self.fallback_to_dict:
            return []

        normalized_word = normalize_digits_and_symbols(word)

        cands: Set[str] = set()
        cands.update(self._candidates_by_ngram(normalized_word, topn=50))
        cands.update(self._candidates_by_phonetic(normalized_word))
        for w in self.hot_dict.get_words():
            if abs(len(w) - len(normalized_word)) <= 3:
                cands.add(w)

        scored = []
        for c in cands:
            dist = weighted_levenshtein(normalized_word, c)
            if dist <= self.config.max_lev_distance * 2:
                sc = self._score_candidate(normalized_word, c)
                scored.append((c, sc))

        scored.sort(key=lambda x: x[1])
        return scored[:k]

    # -----------------------------------------------------------------------
    # Context / phrase correction
    # -----------------------------------------------------------------------

    def _correct_phrase(self, text: str, k: int = 3) -> List[str]:
        """
        Контекстная коррекция многословных фраз.
        Сначала пытаемся через модель, затем fallback по словам.
        """
        if self.corrector is not None:
            try:
                cfg = self._get_model_config()
                outputs = self.corrector(
                    text,
                    max_length=cfg.get("max_length", 128),
                    num_return_sequences=k,
                    num_beams=max(k, cfg.get("num_beams", 5)),
                )
                seen = set()
                results = []
                for out in outputs:
                    cand = out["generated_text"].strip()
                    if cand.lower() not in seen:
                        seen.add(cand.lower())
                        results.append(cand)
                return results
            except Exception as exc:
                _logger.warning("Ошибка коррекции фразы моделью", error=str(exc))

        return [self._fallback_correct(text, topk=k)[0]] if self.fallback_to_dict else [text]

    # -----------------------------------------------------------------------
    # Fallback (single / phrase)
    # -----------------------------------------------------------------------

    def _fallback_correct(self, text: str, topk: int = 1) -> List[str]:
        """
        Словарный fallback для текста.
        Исправляет цифры/спецсимволы, сохраняет регистр, разрешает омографы.
        """
        if not self.fallback_to_dict:
            return [text]

        words_info = extract_words(text)
        if not words_info:
            return [text]

        corrected_words: List[List[Tuple[str, float]]] = []
        for word, _, _ in words_info:
            normalized = normalize_digits_and_symbols(word)
            if self.hot_dict.contains(normalized):
                resolved = resolve_omograph(normalized)
                corrected_words.append([(resolved, 0.0)])
                continue

            cands = self._dict_correction_topk(normalized, k=max(topk, 3))
            if cands:
                corrected_words.append(cands)
            else:
                corrected_words.append([(word, 0.0)])

        result_text = text
        for (orig, start, end), cands in zip(words_info, corrected_words):
            best = cands[0][0]
            if best != orig:
                best = preserve_capitalization(orig, best)
                result_text = safe_replace(result_text, orig, best)

        return [result_text]

    # -----------------------------------------------------------------------
    # Публичный API (обратная совместимость)
    # -----------------------------------------------------------------------


    def _update_dict_from_result(self, result_texts: List[str]) -> None:
        """Добавляет слова из успешно исправленного результата в словарь."""
        if not self.auto_update:
            return
        for text in result_texts:
            words = re.findall(r"[а-яё]+", text.lower())
            for w in words:
                if len(w) > 2:
                    self.hot_dict.add(w)
        self._rebuild_indices()

    def correct(self, text: str, max_length: int = 128) -> List[str]:
        """
        Исправляет один запрос.
        Возвращает список: [исходный_текст, исправленный_текст] или [текст] если без изменений.
        """
        if not text or not text.strip():
            return [text]
        res = self._correct_cached(text, max_length, 1, self.current_model_name)
        if len(res) > 1:
            self._update_dict_from_result([res[-1]])
        else:
            self._update_dict_from_result([res[0]])
        return res

    def correct_topk(self, text: str, k: int = 3, max_length: int = 128) -> List[str]:
        """
        Возвращает топ-k наиболее вероятных исправлений.
        """
        if not text or not text.strip():
            return [text]
        k = max(1, k)
        res = self._correct_cached_topk(text, max_length, k, self.current_model_name)
        self._update_dict_from_result(res)
        return res

    # -----------------------------------------------------------------------
    # Внутренние кэшированные методы
    # -----------------------------------------------------------------------

    @lru_cache(maxsize=4096)
    def _correct_cached(self, text: str, max_length: int, num_return: int, model_name: str) -> List[str]:
        return self._correct_impl(text, max_length, num_return)

    @lru_cache(maxsize=4096)
    def _correct_cached_topk(self, text: str, max_length: int, k: int, model_name: str) -> List[str]:
        return self._correct_impl(text, max_length, k)

    def _correct_impl(self, text: str, max_length: int, k: int) -> List[str]:
        text_stripped = text.strip()
        if not text_stripped:
            return [text]

        results: List[str] = []
        try:
            if self.corrector is not None:
                cfg = self._get_model_config()
                outputs = self.corrector(
                    text_stripped,
                    max_length=max_length,
                    num_return_sequences=k,
                    num_beams=max(k, cfg.get("num_beams", 5)),
                )
                seen = set()
                for out in outputs:
                    cand = out["generated_text"].strip()
                    if cand.lower() not in seen:
                        seen.add(cand.lower())
                        results.append(cand)
            else:
                results = []
        except Exception as exc:
            _logger.warning("Ошибка модели при исправлении", error=str(exc))
            results = []

        if not results and self.fallback_to_dict:
            results = self._fallback_correct(text_stripped, topk=k)

        unique = []
        seen_lower = set()
        for r in results:
            rl = r.lower()
            if rl not in seen_lower:
                seen_lower.add(rl)
                unique.append(r)

        if not unique:
            return [text_stripped]

        if len(unique) == 1 and unique[0] == text_stripped:
            return [text_stripped]

        if k == 1:
            if unique[0] == text_stripped:
                return [text_stripped]
            return [text_stripped, unique[0]]

        return unique

    # -----------------------------------------------------------------------
    # Batch (sync)
    # -----------------------------------------------------------------------

    def correct_batch(self, texts: List[str], max_length: int = 128) -> List[List[str]]:
        """Синхронная пакетная обработка."""
        return [self.correct(t, max_length) for t in texts]

    # -----------------------------------------------------------------------
    # Batch (async)
    # -----------------------------------------------------------------------

    async def correct_batch_async(
        self,
        texts: List[str],
        max_length: int = 128,
        k: int = 1,
    ) -> List[List[str]]:
        """Асинхронная пакетная обработка."""
        loop = asyncio.get_running_loop()
        max_batch = self.config.get("api", "max_batch_size", default=32)
        chunks = [texts[i : i + max_batch] for i in range(0, len(texts), max_batch)]

        all_results: List[List[str]] = []
        for chunk in chunks:
            tasks = [
                loop.run_in_executor(
                    None,
                    self._correct_sync_wrapper,
                    t,
                    max_length,
                    k,
                )
                for t in chunk
            ]
            chunk_results = await asyncio.gather(*tasks)
            all_results.extend(chunk_results)
        return all_results

    def _correct_sync_wrapper(self, text: str, max_length: int, k: int) -> List[str]:
        """Обёртка для совместимости с run_in_executor."""
        if k == 1:
            return self.correct(text, max_length)
        return self.correct_topk(text, k, max_length)

    # -----------------------------------------------------------------------
    # Метрики
    # -----------------------------------------------------------------------

    def evaluate(self, dataset: List[Tuple[str, str, str]]) -> str:
        """
        Оценивает качество на тестовом наборе.
        dataset: [(опечатка, ожидаемое, категория), ...]
        """
        correct_count = 0
        total = len(dataset)
        for typo, expected, category in dataset:
            result = self.correct(typo)
            corrected = result[-1] if len(result) > 1 else result[0]
            ok = corrected.lower().strip() == expected.lower().strip()
            if ok:
                correct_count += 1
            self.metrics.log_correction(typo, corrected, expected)

        acc = correct_count / total if total else 0.0
        report = (
            f"=== Evaluation Report ===\n"
            f"Total samples: {total}\n"
            f"Correct: {correct_count}\n"
            f"Accuracy: {acc:.3f}\n"
            f"{self.metrics.report()}"
        )
        return report

    # -----------------------------------------------------------------------
    # Экспорт / импорт словаря
    # -----------------------------------------------------------------------

    def export_dictionary(self, path: str) -> None:
        """Экспортирует текущий словарь в файл."""
        save_dictionary(self.hot_dict.get_words(), path)

    def import_dictionary(self, path: str) -> None:
        """Импортирует словарь из файла и перестраивает индексы."""
        words = load_dictionary(path)
        save_dictionary(words, self._dict_path)
        self._rebuild_indices()

    # -----------------------------------------------------------------------
    # Очистка кэша
    # -----------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Очищает LRU-кэш исправлений."""
        self._correct_cached.cache_clear()
        self._correct_cached_topk.cache_clear()

    # -----------------------------------------------------------------------
    # Dunder
    # -----------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"model={self.current_model_name or 'none'}, "
            f"device={'cuda' if self.device != -1 else 'cpu'}, "
            f"dict_size={len(self.hot_dict.get_words())}"
            f")"
        )
