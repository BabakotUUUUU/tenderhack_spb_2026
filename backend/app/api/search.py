"""
API роутер поиска.
"""

import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.parsers.wildberries import WildberriesParser
from app.parsers.ozon import OzonParser
from app.parsers.yandex_market import YandexMarketParser
from app.parsers.runet import RunetParser
from app.parsers.base import ProductItem
from app.nlp.query_processor import process_query

router = APIRouter()
logger = logging.getLogger(__name__)


class SearchResult(BaseModel):
    source: str
    items: list[dict]
    total_found: int
    price_min: Optional[float]
    price_max: Optional[float]
    price_avg: Optional[float]


class SearchResponse(BaseModel):
    original_query: str
    corrected_query: Optional[str]
    was_corrected: bool
    search_variants: list[str]
    region: str
    results: list[SearchResult]
    total_items: int


def _product_to_dict(p: ProductItem) -> dict:
    return {
        "title": p.title,
        "price": p.price,
        "currency": p.currency,
        "image_url": p.image_url,
        "product_url": p.product_url,
        "source": p.source,
        "characteristics": p.characteristics,
        "rating": p.rating,
        "reviews_count": p.reviews_count,
    }


def _build_source_result(source_name: str, items: list[ProductItem]) -> SearchResult:
    prices = [i.price for i in items if i.price and i.price > 0]
    return SearchResult(
        source=source_name,
        items=[_product_to_dict(i) for i in items],
        total_found=len(items),
        price_min=min(prices) if prices else None,
        price_max=max(prices) if prices else None,
        price_avg=round(sum(prices) / len(prices), 2) if prices else None,
    )


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=2, description="Поисковый запрос"),
    region: str = Query("Москва", description="Регион пользователя"),
    limit: int = Query(10, ge=1, le=30, description="Количество результатов на источник"),
):
    # Обрабатываем запрос через NLP
    nlp_result = process_query(q)
    primary = nlp_result["primary_query"]

    logger.info(f"Search: '{q}' -> '{primary}', region={region}")

    # Параллельный запуск всех парсеров
    async def run_wb():
        async with WildberriesParser() as p:
            return await p.search(primary, region, limit)

    async def run_ozon():
        async with OzonParser() as p:
            return await p.search(primary, region, limit)

    async def run_ym():
        async with YandexMarketParser() as p:
            return await p.search(primary, region, limit)

    async def run_runet():
        async with RunetParser() as p:
            return await p.search(primary, region, min(limit, 8))

    wb_items, ozon_items, ym_items, runet_items = await asyncio.gather(
        run_wb(), run_ozon(), run_ym(), run_runet(),
        return_exceptions=True,
    )

    # Обрабатываем исключения
    def safe(result, fallback=[]):
        if isinstance(result, Exception):
            logger.error(f"Parser error: {result}")
            return fallback
        return result or fallback

    results = [
        _build_source_result("Яндекс Маркет", safe(ym_items)),
        _build_source_result("Ozon", safe(ozon_items)),
        _build_source_result("Wildberries", safe(wb_items)),
        _build_source_result("Интернет (Рунет)", safe(runet_items)),
    ]

    total = sum(r.total_found for r in results)

    return SearchResponse(
        original_query=q,
        corrected_query=nlp_result["corrected"] if nlp_result["was_corrected"] else None,
        was_corrected=nlp_result["was_corrected"],
        search_variants=nlp_result["search_variants"],
        region=region,
        results=results,
        total_items=total,
    )
