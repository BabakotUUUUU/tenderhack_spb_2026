"""
Фоновая индексация Рунета.

Запускается при старте backend как asyncio-задача (не блокирует запуск).
Обходит seed-сайты по всем трём категориям и строит локальный BM25-индекс,
чтобы первый пользовательский запрос отвечал из кэша, а не ждал live-crawl.

Логика:
  - При < MIN_INDEXED страниц в индексе → запускаем полный pre-crawl
  - При достаточном индексе → только обновление (refresh)
  - Refresh каждые REFRESH_INTERVAL_HOURS часов
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

MIN_INDEXED = 30          # минимум страниц для пропуска pre-crawl
REFRESH_INTERVAL_H = 2    # часов между refresh-циклами

# Репрезентативные запросы для предварительной индексации.
# Только источники с подтверждённым доступом без прокси:
#   4tochki.ru (шины), foroffice.ru (оргтехника).
# Clothing-сайты (sportmaster, kari) — требуют Playwright + заблокированы.
_SEED_TASKS: list[tuple[str, str]] = [
    # (category, query)
    ("tires",       "шины r16"),
    ("tires",       "зимние шины r17"),
    ("tires",       "летние шины 205 55"),
    ("office_tech", "принтер лазерный"),
    ("office_tech", "мфу"),
    ("office_tech", "картридж"),
]


async def _index_one(category: str, query: str, max_products: int = 8) -> int:
    """Краулит seed-сайты по одному запросу и индексирует найденное."""
    from app.crawler.crawler import crawl_category_seeds
    from app.crawler.extractor import ExtractedProduct
    from app.crawler.priority import yield_to_live_search
    from app.crawler.seeds import get_seeds_for_category
    from app.search_index.indexer import index_product

    # Не мешаем живым поискам
    await yield_to_live_search()

    seeds = get_seeds_for_category(category)
    products: list[ExtractedProduct] = []

    def on_product(ep: ExtractedProduct) -> None:
        products.append(ep)

    try:
        await crawl_category_seeds(
            seeds=seeds[:2],           # не более 2 сайтов за раз
            query=query,
            on_product=on_product,
            max_total=max_products,
            max_concurrent_seeds=1,    # одиночный краулинг чтобы не конкурировать с live
        )
    except Exception as exc:
        logger.debug(f"[BG] crawl error '{query}': {exc}")
        return 0

    indexed = 0
    for ep in products:
        if ep.title and ep.price:
            try:
                if index_product(ep, category=category):
                    indexed += 1
            except Exception:
                pass

    return indexed


async def pre_index_all() -> int:
    """
    Полная предварительная индексация всех категорий.
    Выполняется последовательно, чтобы не перегружать seed-сайты.
    """
    total = 0
    for category, query in _SEED_TASKS:
        try:
            found = await _index_one(category, query, max_products=8)
            total += found
            logger.info(f"[BG] indexed {found:>2} items for '{query}' ({category})")
            await asyncio.sleep(2)   # пауза между категориями
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(f"[BG] error for '{query}': {exc}")
    return total


async def refresh_index() -> int:
    """Обновляет несколько ключевых категорий (легче чем полный pre-crawl)."""
    total = 0
    refresh_tasks = [
        ("tires",       "шины r16"),
        ("office_tech", "ноутбук"),
        ("clothing",    "куртка"),
    ]
    for category, query in refresh_tasks:
        try:
            found = await _index_one(category, query, max_products=6)
            total += found
            await asyncio.sleep(3)
        except Exception:
            pass
    return total


async def background_indexer_loop() -> None:
    """
    Основной цикл фоновой индексации.
    Запускается один раз через asyncio.create_task() из lifespan.
    """
    from app.search_index.db import count_indexed, get_connection, DB_PATH

    logger.info("[BG] Background indexer started")

    # Небольшая задержка чтобы не мешать старту приложения
    await asyncio.sleep(5)

    while True:
        try:
            conn = get_connection(DB_PATH)
            current = count_indexed(conn)
            conn.close()

            if current < MIN_INDEXED:
                logger.info(f"[BG] Index has {current} pages (< {MIN_INDEXED}), starting pre-index")
                total = await pre_index_all()
                logger.info(f"[BG] Pre-index complete: +{total} pages")
            else:
                logger.info(f"[BG] Index has {current} pages, doing refresh")
                total = await refresh_index()
                logger.info(f"[BG] Refresh complete: +{total} pages")

        except asyncio.CancelledError:
            logger.info("[BG] Background indexer cancelled")
            break
        except Exception as exc:
            logger.warning(f"[BG] Indexer loop error: {exc}")

        # Ждём до следующего refresh-цикла
        logger.info(f"[BG] Next refresh in {REFRESH_INTERVAL_H}h")
        await asyncio.sleep(REFRESH_INTERVAL_H * 3600)
