"""
Парсер неформализованных ресурсов Рунета.

Архитектура (self-hosted, без внешних поисковых API):
  1. Определяем категорию товара из запроса (шины/одежда/оргтехника)
  2. Берём seed-сайты для этой категории
  3. Параллельно краулим seed-сайты через собственный async-краулер
  4. Извлекаем товары через extractor (JSON-LD → microdata → OG → heuristics)
  5. Дополняем результатами из локального SQLite FTS5 индекса (BM25)
  6. Все результаты — реальные страницы реальных магазинов

Источник динамический: разные запросы → разные магазины в выдаче.
Запрещённые домены (WB, Ozon, YM и крупные агрегаторы) исключены.
"""

import asyncio
import logging
from typing import Optional

from app.crawler.crawler import crawl_category_seeds
from app.crawler.extractor import ExtractedProduct
from app.crawler.seeds import get_seeds_for_category
from app.parsers.base import BaseParser, ProductItem
from app.search_index.indexer import get_index_connection, index_product
from app.search_index.db import search_fts, count_indexed

logger = logging.getLogger(__name__)


def _extracted_to_product(ep: ExtractedProduct) -> ProductItem:
    """Конвертирует ExtractedProduct в унифицированный ProductItem."""
    chars = dict(ep.characteristics or {})
    if ep.brand and "Бренд" not in chars:
        chars["Бренд"] = ep.brand
    if ep.description and "Описание" not in chars:
        chars["Описание"] = ep.description[:100]

    return ProductItem(
        title=ep.title,
        price=ep.price,
        old_price=ep.old_price,
        image_url=ep.image_url,
        product_url=ep.url,
        source=f"Рунет ({ep.domain})",
        domain=ep.domain,
        characteristics=chars,
    )


def _row_to_product(row: dict) -> ProductItem:
    """Конвертирует строку из SQLite в ProductItem."""
    import json
    chars = {}
    try:
        chars = json.loads(row.get("characteristics_json") or "{}")
    except Exception:
        pass

    domain = row.get("domain", "")
    return ProductItem(
        title=row.get("title", ""),
        price=row.get("price"),
        old_price=row.get("old_price"),
        image_url=row.get("image_url"),
        product_url=row.get("url", ""),
        source=row.get("source_label") or f"Рунет ({domain})",
        domain=domain,
        characteristics=chars,
    )


class RunetParser(BaseParser):
    source_name = "Интернет (Рунет)"
    domain = "runet-crawler"

    async def search(
        self, query: str, region: str = "Москва", limit: int = 8
    ) -> list[ProductItem]:
        from app.nlp.query_processor import _detect_category

        category = _detect_category(query)
        logger.info(f"[Runet] Query='{query}', category={category}, limit={limit}")

        results: list[ProductItem] = []
        seen_urls: set[str] = set()

        def _add(product: ProductItem) -> None:
            url = product.product_url
            if url and url not in seen_urls and len(results) < limit:
                seen_urls.add(url)
                results.append(product)

        # ── Шаг 1: запрос к локальному SQLite FTS5 индексу ──────────────
        try:
            conn = get_index_connection()
            total_indexed = count_indexed(conn)
            logger.info(f"[Runet] Index has {total_indexed} pages")

            if total_indexed > 0:
                rows = search_fts(conn, query, category=category, limit=limit)
                for row in rows:
                    if row.get("price"):  # только с ценой
                        _add(_row_to_product(row))
                logger.info(f"[Runet] Index returned {len(results)} items")
        except Exception as exc:
            logger.warning(f"[Runet] Index search failed: {exc}")

        # ── Шаг 2: живой краулинг seed-сайтов ────────────────────────────
        # Всегда делаем live crawl, чтобы обновлять индекс и получать свежие цены
        remaining = limit - len(results)
        if remaining > 0:
            seeds = get_seeds_for_category(category)
            crawled_products: list[ExtractedProduct] = []

            def on_extracted(ep: ExtractedProduct) -> None:
                crawled_products.append(ep)

            try:
                await asyncio.wait_for(
                    crawl_category_seeds(
                        seeds=seeds[:2],           # не более 2 seeds за раз
                        query=query,
                        on_product=on_extracted,
                        max_total=remaining + 2,
                        max_concurrent_seeds=1,    # последовательно чтобы не перегружать сайт
                    ),
                    timeout=22.0,  # жёсткий потолок для краулера
                )
            except asyncio.TimeoutError:
                logger.warning("[Runet] crawl timeout, using partial results")

            # Индексируем найденное и добавляем в результаты
            for ep in crawled_products:
                if ep.title and ep.price:
                    index_product(ep, category=category)
                    item = _extracted_to_product(ep)
                    _add(item)

            logger.info(
                f"[Runet] Live crawl found {len(crawled_products)} products, "
                f"added {len(results)} total"
            )

        return results[:limit]
