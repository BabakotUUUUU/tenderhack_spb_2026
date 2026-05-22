"""
Собственный лексический ранкер результатов поиска.

Подход: лёгкий scoring без нейросетей и внешних зависимостей.
Соответствует требованию хакатона «предпочтение лёгким решениям».

Алгоритм (собственная реализация):
  1. Токенизация запроса и заголовка товара (unicode split)
  2. Точное совпадение токенов — основной сигнал релевантности
  3. Штрафы/бонусы за качество карточки (цена, фото, характеристики)
  4. Нормализация названия для entity matching (HP M111w = hp m111w)
  5. Дедупликация по нормализованному URL и нормализованному title+price

Веса подобраны эмпирически для товарного поиска.
"""

import logging
import re
from dataclasses import replace
from typing import Optional

from app.parsers.base import ProductItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Токенизация и нормализация
# ---------------------------------------------------------------------------

_NON_WORD = re.compile(r"[^\w\s]")
_SPACES   = re.compile(r"\s+")


def _normalize_title(title: str) -> str:
    """
    Нормализует название для entity matching и дедупликации.
    HP LaserJet M111w  →  hp laserjet m111w
    Canon PIXMA G3420  →  canon pixma g3420
    """
    t = title.lower()
    t = _NON_WORD.sub(" ", t)
    t = _SPACES.sub(" ", t).strip()
    return t


def _tokens(text: str) -> set[str]:
    """Разбивает текст на токены длиной > 2 символов."""
    return {t for t in re.findall(r"[a-zа-яё\d]+", text.lower()) if len(t) > 2}


def _item_text(item: ProductItem) -> str:
    """Собирает поисковый текст из заголовка и первых характеристик."""
    parts = [item.title or ""]
    if item.characteristics:
        chars_str = " ".join(
            f"{k} {v}" for k, v in list(item.characteristics.items())[:3]
        )
        parts.append(chars_str)
    return " ".join(parts)[:256]


# ---------------------------------------------------------------------------
# Качество карточки
# ---------------------------------------------------------------------------

def _quality_score(item: ProductItem) -> float:
    """
    Бонус/штраф за полноту карточки товара.
    Диапазон: примерно -0.25 … +0.25
    """
    score = 0.0
    # Цена — ключевой атрибут
    if item.price and item.price > 0:
        score += 0.12
    else:
        score -= 0.10
    # Изображение
    if item.image_url:
        score += 0.05
    else:
        score -= 0.05
    # Характеристики
    if item.characteristics:
        score += min(len(item.characteristics), 5) * 0.02
    # Короткий/мусорный заголовок
    title_len = len((item.title or "").strip())
    if title_len < 8:
        score -= 0.20
    elif title_len > 20:
        score += 0.03
    return score


# ---------------------------------------------------------------------------
# Лексический scoring (собственная реализация)
# ---------------------------------------------------------------------------

def _lexical_score(query: str, item: ProductItem) -> float:
    """
    Оценивает релевантность товара запросу по точному совпадению токенов.

    Формула:
      base = |tokens(query) ∩ tokens(item)| / |tokens(query)|
      score = base * 0.75 + quality_score(item)

    Результат зажат в [0, 1].
    """
    query_tokens = _tokens(query)
    item_tokens  = _tokens(_item_text(item))

    if not query_tokens:
        base = 0.0
    else:
        overlap = len(query_tokens & item_tokens)
        base = overlap / len(query_tokens)

    raw = base * 0.75 + _quality_score(item)
    return max(0.0, min(1.0, raw))


def _explain(query: str, item: ProductItem, score: float) -> str:
    """Человекочитаемое объяснение score для отладки."""
    hits = _tokens(query) & _tokens(_item_text(item))
    parts: list[str] = []
    if hits:
        parts.append("слова: " + ", ".join(sorted(hits)[:5]))
    if item.price:
        parts.append("есть цена")
    if item.image_url:
        parts.append("есть фото")
    if item.characteristics:
        parts.append(f"характеристики ({len(item.characteristics)})")
    return "; ".join(parts) or f"score={score:.2f}"


# ---------------------------------------------------------------------------
# Дедупликация и фильтрация мусора
# ---------------------------------------------------------------------------

def _dedupe_and_filter(items: list[ProductItem]) -> list[ProductItem]:
    """
    Удаляет дубликаты и очевидный мусор.

    Дедупликация:
      1. По нормализованному URL (без query string)
      2. По нормализованному title + price + source

    Фильтрация:
      - Нет заголовка или URL
      - Слишком короткий заголовок (< 4 символов)
      - URL похож на страницу категории/поиска, не карточку товара
    """
    seen_urls: set[str] = set()
    seen_keys: set[tuple[str, Optional[int], str]] = set()
    result: list[ProductItem] = []

    for item in items:
        title = (item.title or "").strip()
        url   = (item.product_url or "").strip()

        # Фильтр мусора
        if not title or not url or len(title) < 4:
            continue

        url_lower = url.lower().split("?")[0].rstrip("/")

        # Фильтр страниц-категорий (не карточек)
        is_category = (
            any(m in url_lower for m in ("/catalog/?", "/category/", "/search?", "/search/"))
            and not any(
                m in url_lower
                for m in (
                    "wildberries.ru/catalog/",
                    "ozon.ru/product",
                    "market.yandex.ru/product",
                )
            )
        )
        if is_category:
            continue

        # Дедуп по URL
        if url_lower in seen_urls:
            continue
        seen_urls.add(url_lower)

        # Дедуп по нормализованному title + price (entity matching)
        norm_title = _normalize_title(title)[:100]
        price_key  = int(item.price) if item.price else None
        key        = (norm_title, price_key, item.source)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        result.append(item)

    return result


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------

def rank_items(query: str, items: list[ProductItem]) -> list[ProductItem]:
    """
    Сортирует товары по релевантности к запросу (лексический подход).

    Шаги:
      1. Дедупликация и фильтрация мусора
      2. Лексический scoring (токенное пересечение + качество карточки)
      3. Сортировка по убыванию score
      4. Запись score и объяснения в поля ProductItem
    """
    if not items or not query.strip():
        return items

    items = _dedupe_and_filter(items)
    if not items:
        return []

    scored: list[tuple[ProductItem, float]] = []
    for item in items:
        score = round(_lexical_score(query, item), 4)
        scored.append((item, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    return [
        replace(
            item,
            relevance_score=score,
            relevance_explanation=_explain(query, item, score),
        )
        for item, score in scored
    ]


def warmup() -> None:
    """Stub для совместимости — лексический ранкер не требует прогрева."""
    logger.info("[Ranker] Lexical ranker ready (no warmup needed)")
