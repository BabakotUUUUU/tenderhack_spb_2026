"""
Гибридный Spell Checker: PriceHunt NLP + BART Spell Checker.

Pipeline (по скорости, от быстрого к медленному):
  1. PriceHunt fast-path:
     - EN->RU keyboard layout fix (< 1 ms)
     - Tire pattern normalization (< 1 ms)
     - TYPO_MAP O(1) hash lookup (< 0.1 ms)
     - rapidfuzz fuzzy match с порогом 82% (< 5 ms)
     - pymorphy3 лемматизация
  2. BART algorithmic fallback:
     - N-gram Jaccard similarity для быстрого поиска кандидатов
     - Phonetic index (Soundex / Metaphone) для звуковых ошибок
     - Weighted Levenshtein с клавиатурными весами
     - Hot-reload dictionary
  3. BART neural (опционально, если torch/transformers установлены):
     - Seq2seq denoising через facebook/bart-base
     - Mask-based span correction через <mask>
  4. Всегда:
     - Synonym expansion из SYNONYM_MAP
     - Metrics collection (precision / recall / F1)
     - Async batch processing
     - LRU cache

Доп. библиотеки (pip):
    torch transformers sentencepiece accelerate
    pymorphy3 pymorphy3-dicts-ru rapidfuzz
    pyyaml structlog fastapi uvicorn httpx
    pytest pytest-asyncio
"""

from __future__ import annotations

import os
import re
import json
import asyncio
import threading
import hashlib
import random
from collections import defaultdict
from functools import lru_cache
from typing import List, Optional, Dict, Set, Tuple, Any

import yaml

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    torch = None  # type: ignore
    _HAS_TORCH = False

try:
    from transformers import pipeline, BartForConditionalGeneration, BartTokenizerFast
    _HAS_TRANSFORMERS = True
except ImportError:
    pipeline = BartForConditionalGeneration = BartTokenizerFast = None  # type: ignore
    _HAS_TRANSFORMERS = False

try:
    import structlog
    _logger = structlog.get_logger()
except ImportError:
    import logging as _logging
    _logger = _logging.getLogger("hybrid_spell_checker")

from src.hybrid_utils import (
    # PriceHunt
    SYNONYM_MAP, SYNONYM_REVERSE, TYPO_MAP,
    fix_keyboard_layout, fix_tire_pattern, lemmatize_word, lemmatize_query,
    KNOWN_RU_ROOTS,
    # BART algorithms
    weighted_levenshtein, keyboard_distance,
    phonetic_key, phonetic_similarity, build_phonetic_index,
    ngrams, ngram_similarity,
    normalize_digits_and_symbols, preserve_capitalization,
    extract_words, safe_replace, resolve_omograph,
    load_dictionary, save_dictionary, HotReloadDictionary,
    MetricsCollector,
)


_MASK_TOKEN = "<mask>"


# ---------------------------------------------------------------------------
# BART Noise Corruptor (synthetic data augmentation)
# ---------------------------------------------------------------------------

class BartNoiseCorruptor:
    """Генератор BART-style noise для data augmentation."""

    def __init__(
        self,
        mask_token: str = _MASK_TOKEN,
        poisson_lambda: float = 3.0,
        permute_prob: float = 1.0,
        delete_prob: float = 0.1,
    ):
        self.mask_token = mask_token
        self.poisson_lambda = poisson_lambda
        self.permute_prob = permute_prob
        self.delete_prob = delete_prob

    def text_infilling(self, text: str, mask_ratio: float = 0.3) -> str:
        words = text.split()
        if not words:
            return text
        try:
            import numpy as np
        except ImportError:
            return text
        n_masks = max(1, int(len(words) * mask_ratio))
        spans = self._sample_spans(len(words), n_masks)
        result = []
        i = 0
        while i < len(words):
            if any(start <= i < end for start, end in spans):
                end = next(end for start, end in spans if start <= i < end)
                result.append(self.mask_token)
                i = end
            else:
                result.append(words[i])
                i += 1
        return " ".join(result)

    def _sample_spans(self, length: int, n_spans: int) -> List[Tuple[int, int]]:
        try:
            import numpy as np
        except ImportError:
            return []
        starts = sorted(random.sample(range(length), min(n_spans, length)))
        spans = []
        for s in starts:
            span_len = max(1, np.random.poisson(self.poisson_lambda))
            e = min(s + span_len, length)
            spans.append((s, e))
        spans.sort()
        merged = []
        for s, e in spans:
            if merged and s < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))
        return merged

    def sentence_permutation(self, text: str) -> str:
        if random.random() > self.permute_prob:
            return text
        sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
        if len(sents) < 2:
            return text
        random.shuffle(sents)
        return " ".join(sents)

    def token_deletion(self, text: str) -> str:
        words = text.split()
        return " ".join([w for w in words if random.random() > self.delete_prob])

    def corrupt(self, text: str) -> str:
        text = self.text_infilling(text)
        text = self.token_deletion(text)
        if len(text.split()) > 3:
            text = self.sentence_permutation(text)
        return text


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

class HybridConfig:
    def __init__(self, path: str = "config_hybrid.yaml"):
        self.path = path
        self._cfg = self._load()

    def _load(self) -> dict:
        default = {
            "models": {
                "primary": {"name": "facebook/bart-base", "use_gpu": True,
                            "max_length": 128, "num_beams": 5, "temperature": 1.0},
                "fast": {"name": "facebook/bart-base", "use_gpu": False,
                         "max_length": 64, "num_beams": 3, "temperature": 1.0},
            },
            "fallback": {
                "enabled": True,
                "dictionary_path": "data/hybrid_dict.txt",
                "auto_update": True,
                "max_levenshtein_distance": 5,
                "hot_reload_interval": 300,
            },
            "pricehunt": {
                "rapidfuzz_threshold": 82,
                "max_variants": 4,
                "enable_tire_pattern": True,
                "enable_keyboard_layout_fix": True,
            },
            "keyboard": {"weight_adjacent": 1.0, "weight_same_row": 1.5, "weight_other": 3.0},
            "phonetic": {"enabled": True, "algorithm": "combined"},
            "cache": {"maxsize": 4096},
            "logging": {
                "level": "INFO",
                "metrics_enabled": True,
                "metrics_file": "logs/hybrid_metrics.jsonl",
            },
            "api": {"host": "0.0.0.0", "port": 8000, "workers": 1, "max_batch_size": 32},
        }
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                if loaded:
                    default.update(loaded)
            except Exception as exc:
                _logger.warning("config load failed", error=str(exc))
        return default

    def get(self, *keys: str, default: Any = None) -> Any:
        d = self._cfg
        for k in keys:
            if isinstance(d, dict) and k in d:
                d = d[k]
            else:
                return default
        return d


# ---------------------------------------------------------------------------
# Hybrid Spell Checker
# ---------------------------------------------------------------------------

class HybridSpellChecker:
    """
    Гибридный корректор: PriceHunt fast-path + BART algorithms + BART neural.

    Публичный API:
        correct(text) -> List[str]
        correct_topk(text, k=3) -> List[str]
        correct_batch(texts) -> List[List[str]]
        correct_batch_async(texts) -> List[List[str]]
        process_query(text) -> dict (PriceHunt-style с synonyms)
        switch_model(mode)
        evaluate(dataset) -> str
    """

    def __init__(
        self,
        use_gpu: bool = True,
        config_path: str = "config_hybrid.yaml",
        model_mode: str = "primary",
        auto_update: bool = True,
    ):
        self.config = HybridConfig(config_path)
        self.model_mode = model_mode
        self.auto_update = auto_update and self.config.get("fallback", "auto_update", default=True)
        self._lock = threading.RLock()

        # PriceHunt thresholds
        self._rapidfuzz_threshold = self.config.get("pricehunt", "rapidfuzz_threshold", default=82)
        self._max_variants = self.config.get("pricehunt", "max_variants", default=4)
        self._enable_tire = self.config.get("pricehunt", "enable_tire_pattern", default=True)
        self._enable_layout = self.config.get("pricehunt", "enable_keyboard_layout_fix", default=True)

        # Model
        self.corrector: Optional[Any] = None
        self.tokenizer: Optional[Any] = None
        self.model: Optional[Any] = None
        self.current_model_name: str = ""
        self.device: int = -1
        if _HAS_TORCH and use_gpu:
            self.device = 0 if torch.cuda.is_available() else -1  # type: ignore

        # BART corruptor
        self.bart_corruptor = BartNoiseCorruptor()

        # Dictionary
        self._dict_path = self.config.get("fallback", "dictionary_path", default="data/hybrid_dict.txt")
        os.makedirs(os.path.dirname(self._dict_path) or ".", exist_ok=True)
        if not os.path.exists(self._dict_path):
            _default = set(TYPO_MAP.values()) | set(SYNONYM_MAP.keys())
            _default |= {"телефон", "компьютер", "мышь", "клавиатура", "экран",
                         "наушники", "зарядка", "ноутбук", "планшет", "смартфон",
                         "принтер", "сканер", "роутер", "модем", "колонки",
                         "микрофон", "вебкамера", "монитор", "apple", "samsung",
                         "nokia", "игр", "для", "дюйма", "беспроводная"}
            save_dictionary(_default, self._dict_path)

        self.hot_dict = HotReloadDictionary(
            self._dict_path,
            interval_sec=self.config.get("fallback", "hot_reload_interval", default=300),
        )

        # Indices
        self._ngram_index: Dict[str, Set[str]] = defaultdict(set)
        self._phonetic_index: Dict[str, Set[str]] = {}
        self._rebuild_indices()

        # Metrics
        self.metrics = MetricsCollector(
            self.config.get("logging", "metrics_file", default="logs/hybrid_metrics.jsonl")
        )

        # Load model
        self._load_model()

    # -----------------------------------------------------------------------
    # Model loading
    # -----------------------------------------------------------------------

    def _get_model_config(self) -> dict:
        if self.model_mode == "fast":
            return self.config.get("models", "fast", default={})
        return self.config.get("models", "primary", default={})

    def switch_model(self, mode: str) -> None:
        with self._lock:
            if mode not in {"primary", "fast", "auto"}:
                raise ValueError(f"Неверный режим: {mode}")
            self.model_mode = mode
            self._cleanup_model()
            self._load_model()

    def _cleanup_model(self) -> None:
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
        if not _HAS_TRANSFORMERS or not _HAS_TORCH:
            _logger.warning("transformers/torch недоступны — neural path отключён")
            self.corrector = None
            return
        cfg = self._get_model_config()
        self.current_model_name = cfg.get("name", "facebook/bart-base")
        try:
            _logger.info("Загрузка BART", model=self.current_model_name)
            self.tokenizer = BartTokenizerFast.from_pretrained(self.current_model_name)
            self.model = BartForConditionalGeneration.from_pretrained(self.current_model_name)
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
            _logger.info("BART загружен")
        except Exception as exc:
            _logger.warning("BART failed", error=str(exc))
            self.corrector = None

    # -----------------------------------------------------------------------
    # Indices
    # -----------------------------------------------------------------------

    def _rebuild_indices(self) -> None:
        words = self.hot_dict.get_words()
        self._ngram_index = defaultdict(set)
        for w in words:
            for ng in ngrams(w, n=2):
                self._ngram_index[ng].add(w)
        if self.config.get("phonetic", "enabled", default=True):
            self._phonetic_index = build_phonetic_index(
                list(words), self.config.get("phonetic", "algorithm", default="combined")
            )

    def _candidates_by_ngram(self, word: str, topn: int = 20) -> List[str]:
        word_ngs = ngrams(word, n=2)
        counter: Dict[str, int] = defaultdict(int)
        for ng in word_ngs:
            for cand in self._ngram_index.get(ng, set()):
                counter[cand] += 1
        return [c for c, _ in sorted(counter.items(), key=lambda x: (-x[1], x[0]))[:topn]]

    def _candidates_by_phonetic(self, word: str) -> List[str]:
        if not self.config.get("phonetic", "enabled", default=True):
            return []
        key = phonetic_key(word, self.config.get("phonetic", "algorithm", default="combined"))
        return list(self._phonetic_index.get(key, set()))

    # -----------------------------------------------------------------------
    # Scoring
    # -----------------------------------------------------------------------

    def _score_candidate(self, word: str, candidate: str) -> float:
        w = word.lower()
        c = candidate.lower()
        lev = weighted_levenshtein(
            w, c,
            weight_adjacent=self.config.get("keyboard", "weight_adjacent", default=1.0),
            weight_same_row=self.config.get("keyboard", "weight_same_row", default=1.5),
            weight_other=self.config.get("keyboard", "weight_other", default=3.0),
        )
        ng_sim = ngram_similarity(w, c, n=2)
        ph_sim = phonetic_similarity(w, c, self.config.get("phonetic", "algorithm", default="combined"))
        return lev * 1.0 + (1.0 - ng_sim) * 2.0 + (1.0 - ph_sim) * 2.0

    def _dict_correction_topk(self, word: str, k: int = 3) -> List[Tuple[str, float]]:
        normalized = normalize_digits_and_symbols(word)
        cands: Set[str] = set()
        cands.update(self._candidates_by_ngram(normalized, topn=50))
        cands.update(self._candidates_by_phonetic(normalized))
        for w in self.hot_dict.get_words():
            if abs(len(w) - len(normalized)) <= 3:
                cands.add(w)
        scored = []
        max_lev = self.config.get("fallback", "max_levenshtein_distance", default=5) * 2
        for c in cands:
            dist = weighted_levenshtein(normalized, c)
            if dist <= max_lev:
                sc = self._score_candidate(normalized, c)
                scored.append((c, sc))
        scored.sort(key=lambda x: x[1])
        return scored[:k]

    # -----------------------------------------------------------------------
    # PriceHunt fast-path
    # -----------------------------------------------------------------------

    def _pricehunt_correct(self, text: str) -> Tuple[str, bool]:
        """
        Быстрый путь PriceHunt.
        Возвращает (corrected, was_corrected).
        """
        original = text
        was_corrected = False

        # 1. Keyboard layout fix
        if self._enable_layout:
            text = fix_keyboard_layout(text)
            if text != original:
                was_corrected = True

        # 2. Tire pattern
        if self._enable_tire:
            text = fix_tire_pattern(text)
            if text != original:
                was_corrected = True

        normalized = text.strip().lower()
        normalized = re.sub(r"\s+", " ", normalized)

        # 3. TYPO_MAP direct lookup
        if normalized in TYPO_MAP:
            return preserve_capitalization(text, TYPO_MAP[normalized]), True

        lemmatized = lemmatize_query(normalized)
        if lemmatized != normalized and lemmatized in TYPO_MAP:
            return preserve_capitalization(text, TYPO_MAP[lemmatized]), True

        # 4. rapidfuzz fuzzy match (по словам)
        try:
            from rapidfuzz import process, fuzz
            all_known = (
                list(TYPO_MAP.keys())
                + list(SYNONYM_MAP.keys())
                + [s for syns in SYNONYM_MAP.values() for s in syns]
            )
            words = normalized.split()
            corrected_words = []
            for word in words:
                if len(word) <= 3:
                    corrected_words.append(word)
                    continue
                lemma = lemmatize_word(word)
                match = process.extractOne(
                    lemma, all_known,
                    scorer=fuzz.ratio,
                    score_cutoff=self._rapidfuzz_threshold,
                )
                if match and match[0] != lemma:
                    canonical = SYNONYM_REVERSE.get(match[0], match[0])
                    corrected_words.append(canonical)
                    was_corrected = True
                else:
                    corrected_words.append(lemma if lemma != word else word)
            result = " ".join(corrected_words)
            return result, was_corrected
        except ImportError:
            pass

        return text, was_corrected

    # -----------------------------------------------------------------------
    # BART algorithmic fallback (word-level)
    # -----------------------------------------------------------------------

    def _algorithmic_correct(self, text: str) -> str:
        """Fallback на n-gram + phonetic + weighted Levenshtein."""
        words_info = extract_words(text)
        if not words_info:
            return text

        corrected_words: List[List[Tuple[str, float]]] = []
        for word, _, _ in words_info:
            normalized = normalize_digits_and_symbols(word)
            if self.hot_dict.contains(normalized):
                resolved = resolve_omograph(normalized)
                corrected_words.append([(resolved, 0.0)])
                continue
            cands = self._dict_correction_topk(normalized, k=3)
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
        return result_text

    # -----------------------------------------------------------------------
    # BART neural
    # -----------------------------------------------------------------------

    def _bart_correct(self, text: str, max_length: int, k: int) -> List[str]:
        if self.corrector is None:
            return []
        try:
            cfg = self._get_model_config()
            outputs = self.corrector(
                text,
                max_length=max_length,
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
            _logger.warning("BART correction failed", error=str(exc))
            return []

    # -----------------------------------------------------------------------
    # Core correction pipeline
    # -----------------------------------------------------------------------

    def _correct_impl(self, text: str, max_length: int, k: int) -> List[str]:
        text_stripped = text.strip()
        if not text_stripped:
            return [text]

        results: List[str] = []

        # 1. PriceHunt fast-path
        ph_result, ph_fixed = self._pricehunt_correct(text_stripped)
        if ph_fixed:
            results.append(ph_result)

        # 2. BART neural (если доступен и fast-path не дал результат)
        if not results and self.corrector is not None:
            neural = self._bart_correct(text_stripped, max_length, k)
            results.extend(neural)

        # 3. Algorithmic fallback
        if not results:
            algo = self._algorithmic_correct(text_stripped)
            if algo != text_stripped:
                results.append(algo)

        # 4. If still nothing, use lemmatized version
        if not results:
            lemma = lemmatize_query(text_stripped)
            if lemma != text_stripped:
                results.append(lemma)

        # Deduplicate
        unique = []
        seen_lower = set()
        for r in results:
            rl = r.lower()
            if rl not in seen_lower:
                seen_lower.add(rl)
                unique.append(r)

        if not unique:
            return [text_stripped]

        # k=1 format: [original, corrected]
        if k == 1:
            if unique[0] == text_stripped:
                return [text_stripped]
            return [text_stripped, unique[0]]

        return unique

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def correct(self, text: str, max_length: int = 128) -> List[str]:
        if not text or not text.strip():
            return [text]
        res = self._correct_cached(text, max_length, 1, self.current_model_name)
        self._update_dict(res)
        return res

    def correct_topk(self, text: str, k: int = 3, max_length: int = 128) -> List[str]:
        if not text or not text.strip():
            return [text]
        k = max(1, k)
        res = self._correct_cached_topk(text, max_length, k, self.current_model_name)
        self._update_dict(res)
        return res

    def process_query(self, raw_query: str) -> dict:
        """PriceHunt-style полный pipeline с synonyms."""
        corrected, was_corrected = self._pricehunt_correct(raw_query)
        # Если PriceHunt не исправил, пробуем algorithmic/BART
        if not was_corrected and corrected.lower().strip() == raw_query.lower().strip():
            alt = self.correct(raw_query)
            if len(alt) > 1:
                corrected = alt[-1]
                was_corrected = True

        variants = self.expand_synonyms(corrected)
        return {
            "original": raw_query,
            "corrected": corrected,
            "was_corrected": was_corrected,
            "search_variants": variants,
            "used_synonyms": self.used_synonyms(corrected),
            "expanded_queries": variants,
            "primary_query": corrected,
            "category": self._detect_category(corrected),
        }

    def expand_synonyms(self, query: str) -> List[str]:
        normalized = query.strip().lower()
        normalized = re.sub(r"\s+", " ", normalized)
        variants: Set[str] = {normalized}

        canonical = SYNONYM_REVERSE.get(normalized)
        if canonical:
            variants.add(canonical)

        if normalized in SYNONYM_MAP:
            for syn in SYNONYM_MAP[normalized][:2]:
                variants.add(syn)

        for canonical_term, syns in SYNONYM_MAP.items():
            if canonical_term in normalized:
                for syn in syns[:2]:
                    variants.add(normalized.replace(canonical_term, syn))

        lemmatized = lemmatize_query(normalized)
        if lemmatized != normalized:
            variants.add(lemmatized)
        canonical2 = SYNONYM_REVERSE.get(lemmatized)
        if canonical2:
            variants.add(canonical2)

        return list(variants)[:self._max_variants]

    def used_synonyms(self, query: str) -> Dict[str, List[str]]:
        normalized = query.strip().lower()
        used: Dict[str, List[str]] = {}
        for canonical, syns in SYNONYM_MAP.items():
            if canonical in normalized:
                used[canonical] = syns[:4]
            if canonical == SYNONYM_REVERSE.get(normalized):
                used[canonical] = SYNONYM_MAP.get(canonical, [])[:4]
        return used

    @staticmethod
    def _detect_category(query: str) -> str:
        q = query.lower()
        if any(w in q for w in ["шин", "резин", "покрышк", " r1", " r2", "/"]):
            return "tires"
        if any(w in q for w in ["ноутбук", "принтер", "монитор", "мфу", "клавиатур",
                                   "сканер", "проектор", "роутер", "картридж"]):
            return "office_tech"
        if any(w in q for w in ["куртк", "пальто", "брюки", "платье", "кроссовк",
                                   "ботинк", "рубашк", "свитер", "джинс", "футболк"]):
            return "clothing"
        return "general"

    # -----------------------------------------------------------------------
    # Cache
    # -----------------------------------------------------------------------

    @lru_cache(maxsize=4096)
    def _correct_cached(self, text: str, max_length: int, num_return: int, model_name: str) -> List[str]:
        return self._correct_impl(text, max_length, num_return)

    @lru_cache(maxsize=4096)
    def _correct_cached_topk(self, text: str, max_length: int, k: int, model_name: str) -> List[str]:
        return self._correct_impl(text, max_length, k)

    def clear_cache(self) -> None:
        self._correct_cached.cache_clear()
        self._correct_cached_topk.cache_clear()

    # -----------------------------------------------------------------------
    # Dict update
    # -----------------------------------------------------------------------

    def _update_dict(self, results: List[str]) -> None:
        if not self.auto_update:
            return
        changed = False
        for text in results:
            words = re.findall(r"[а-яё]+", text.lower())
            for w in words:
                if len(w) > 2:
                    before = len(self.hot_dict.get_words())
                    self.hot_dict.add(w)
                    after = len(self.hot_dict.get_words())
                    if after != before:
                        changed = True
        if changed:
            self._rebuild_indices()

    # -----------------------------------------------------------------------
    # Batch
    # -----------------------------------------------------------------------

    def correct_batch(self, texts: List[str], max_length: int = 128) -> List[List[str]]:
        return [self.correct(t, max_length) for t in texts]

    async def correct_batch_async(
        self, texts: List[str], max_length: int = 128, k: int = 1
    ) -> List[List[str]]:
        loop = asyncio.get_running_loop()
        max_batch = self.config.get("api", "max_batch_size", default=32)
        chunks = [texts[i:i + max_batch] for i in range(0, len(texts), max_batch)]
        all_results: List[List[str]] = []
        for chunk in chunks:
            tasks = [
                loop.run_in_executor(None, self._correct_sync_wrapper, t, max_length, k)
                for t in chunk
            ]
            all_results.extend(await asyncio.gather(*tasks))
        return all_results

    def _correct_sync_wrapper(self, text: str, max_length: int, k: int) -> List[str]:
        if k == 1:
            return self.correct(text, max_length)
        return self.correct_topk(text, k, max_length)

    # -----------------------------------------------------------------------
    # Synthetic data generation
    # -----------------------------------------------------------------------

    def generate_synthetic_typos(self, clean_texts: List[str], noise_prob: float = 0.3) -> List[Tuple[str, str]]:
        pairs = []
        for text in clean_texts:
            if random.random() < noise_prob:
                noisy = self.bart_corruptor.corrupt(text)
                pairs.append((noisy, text))
        return pairs

    # -----------------------------------------------------------------------
    # Evaluation
    # -----------------------------------------------------------------------

    def evaluate(self, dataset: List[Tuple[str, str, str]]) -> str:
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
        return (
            f"=== Hybrid Evaluation Report ===\n"
            f"Total samples: {total}\n"
            f"Correct: {correct_count}\n"
            f"Accuracy: {acc:.3f}\n"
            f"{self.metrics.report()}"
        )

    # -----------------------------------------------------------------------
    # Export / Import
    # -----------------------------------------------------------------------

    def export_dictionary(self, path: str) -> None:
        save_dictionary(self.hot_dict.get_words(), path)

    def import_dictionary(self, path: str) -> None:
        words = load_dictionary(path)
        save_dictionary(words, self._dict_path)
        self._rebuild_indices()

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"model={self.current_model_name or 'none'}, "
            f"device={'cuda' if self.device != -1 else 'cpu'}, "
            f"dict_size={len(self.hot_dict.get_words())}"
            f")"
        )
