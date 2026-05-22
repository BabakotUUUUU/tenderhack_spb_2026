"""
Семантический ре-ранкер результатов поиска.

Технология: fastembed (ONNX Runtime) — без PyTorch, без GPU.
Модель: paraphrase-multilingual-MiniLM-L12-v2
  - Размер: ~120 MB (ONNX формат)
  - Поддержка: 50+ языков включая русский
  - Инференс: ~30–80 мс на батч из 20 товаров (CPU)
  - RAM: ~300 MB при загрузке

Почему fastembed, а не sentence-transformers напрямую:
  sentence-transformers тянет PyTorch (~700 MB) — нарушает требование
  хакатона о лёгких решениях. fastembed использует тот же ONNX-экспорт
  модели, но через onnxruntime (~50 MB). Итог: те же качество, в 14×
  меньше зависимостей.

Алгоритм ранжирования:
  1. Кодируем запрос → вектор 384d
  2. Кодируем заголовок каждого товара → вектор 384d
  3. Косинусное сходство(query_vec, title_vec) → score 0..1
  4. Сортируем по убыванию score
  5. Записываем score в поле relevance_score каждого ProductItem
"""

import logging
from dataclasses import replace
from typing import Optional

import numpy as np

from app.parsers.base import ProductItem

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_model = None
_model_attempted = False


def _load_model():
    global _model, _model_attempted
    if _model_attempted:
        return _model
    _model_attempted = True
    try:
        from fastembed import TextEmbedding
        _model = TextEmbedding(
            model_name=MODEL_NAME,
            max_length=128,  # заголовки товаров короткие — 128 токенов хватает
        )
        logger.info(f"[Ranker] Loaded: {MODEL_NAME}")
    except ImportError:
        logger.warning("[Ranker] fastembed не установлен — ранжирование отключено")
    except Exception as exc:
        logger.error(f"[Ranker] Ошибка загрузки модели: {exc}")
    return _model


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom > 1e-9 else 0.0


def rank_items(query: str, items: list[ProductItem]) -> list[ProductItem]:
    """
    Сортирует товары по семантической близости заголовка к запросу.

    Если модель не загружена (fastembed недоступен) — возвращает
    исходный список без изменений, приложение продолжает работу.
    """
    if not items or not query.strip():
        return items

    model = _load_model()
    if model is None:
        return items

    try:
        texts = [query] + [item.title for item in items]
        embeddings = list(model.embed(texts))

        query_vec = np.array(embeddings[0], dtype=np.float32)
        item_vecs = [np.array(e, dtype=np.float32) for e in embeddings[1:]]

        scored: list[tuple[ProductItem, float]] = [
            (item, _cosine(query_vec, vec))
            for item, vec in zip(items, item_vecs)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        return [
            replace(item, relevance_score=round(score, 4))
            for item, score in scored
        ]

    except Exception as exc:
        logger.warning(f"[Ranker] Ошибка ранжирования: {exc}")
        return items


def warmup() -> None:
    """
    Прогрев модели при старте приложения.
    Вызывать из lifespan FastAPI, чтобы первый запрос был быстрым.
    """
    model = _load_model()
    if model is not None:
        try:
            list(model.embed(["прогрев модели"]))
            logger.info("[Ranker] Warmup done")
        except Exception:
            pass
