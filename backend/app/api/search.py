"""API роутер поиска с безопасным запуском источников и TTL-кэшем."""

import asyncio
import logging
import os
import time
from typing import Any, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.ml.ranker import rank_items
from app.nlp.query_processor import SYNONYM_MAP, process_query
from app.parsers.base import ProductItem
from app.parsers.ozon import OzonParser
from app.parsers.runet import RunetParser
from app.parsers.wildberries import WildberriesParser
from app.parsers.yandex_market import YandexMarketParser

router = APIRouter()
logger = logging.getLogger(__name__)

CACHE_ENABLED = os.getenv("CACHE_ENABLED", "true").lower() not in {"0", "false", "no"}
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "1200"))
PARSER_TIMEOUT_SECONDS = float(os.getenv("PARSER_TIMEOUT_SECONDS", "28"))

SOURCE_ORDER = [
    ("yandex_market", "Яндекс Маркет"),
    ("ozon", "Ozon"),
    ("wildberries", "Wildberries"),
    ("runet", "Интернет (Рунет)"),
]

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


class SearchResult(BaseModel):
    source: str
    source_key: str
    status: str = "success"
    items: list[dict]
    total_found: int
    price_min: Optional[float]
    price_max: Optional[float]
    price_avg: Optional[float]
    warning: Optional[str] = None
    cache_hit: bool = False


class SourceSummary(BaseModel):
    source: str
    source_key: str
    status: str
    count: int
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    avg_price: Optional[float] = None
    cache_hit: bool = False
    warning: Optional[str] = None


class QueryMeta(BaseModel):
    original: str
    corrected: str
    expanded: list[str]
    used_synonyms: dict[str, list[str]]


class ResponseMeta(BaseModel):
    original_query: str
    corrected_query: str
    expanded_queries: list[str]
    region: str
    total_count: int
    sources_summary: list[SourceSummary]
    processing_time_ms: int
    warnings: list[str]
    cache_hit: bool


class SearchResponse(BaseModel):
    original_query: str
    corrected_query: Optional[str]
    was_corrected: bool
    search_variants: list[str]
    used_synonyms: dict[str, list[str]] = {}
    category: str = "general"
    region: str
    results: list[SearchResult]
    total_items: int
    query: QueryMeta
    items: list[dict]
    groups: dict[str, list[dict]]
    sources: list[SourceSummary]
    meta: ResponseMeta
    cache_hit: bool


class SearchRequest(BaseModel):
    query: str
    region: str = "Москва"
    limit_per_source: int = 10


def _product_to_dict(p: ProductItem) -> dict[str, Any]:
    return {
        "title": p.title,
        "price": p.price,
        "id": p.id,
        "currency": p.currency,
        "old_price": p.old_price,
        "image_url": p.image_url,
        "product_url": p.product_url,
        "source": p.source,
        "domain": p.domain,
        "characteristics": p.characteristics or {},
        "availability": p.availability,
        "rating": p.rating,
        "reviews_count": p.reviews_count,
        "relevance_score": p.relevance_score,
        "relevance_explanation": p.relevance_explanation,
    }


def _dict_to_product(data: dict[str, Any]) -> ProductItem:
    return ProductItem(
        title=data.get("title") or "",
        price=data.get("price"),
        id=data.get("id"),
        currency=data.get("currency") or "RUB",
        old_price=data.get("old_price"),
        image_url=data.get("image_url"),
        product_url=data.get("product_url") or "",
        source=data.get("source") or "",
        domain=data.get("domain"),
        characteristics=data.get("characteristics") or {},
        availability=data.get("availability"),
        rating=data.get("rating"),
        reviews_count=data.get("reviews_count"),
        relevance_score=data.get("relevance_score"),
        relevance_explanation=data.get("relevance_explanation"),
    )


def _cache_key(source_key: str, query: str, region: str, limit: int) -> str:
    return f"{source_key}:{query.lower().strip()}:{region.lower().strip()}:{limit}"


def _cache_get(source_key: str, query: str, region: str, limit: int) -> Optional[list[ProductItem]]:
    if not CACHE_ENABLED:
        return None
    key = _cache_key(source_key, query, region, limit)
    cached = _CACHE.get(key)
    if not cached:
        return None
    expires_at, payload = cached
    if expires_at < time.monotonic():
        _CACHE.pop(key, None)
        return None
    return [_dict_to_product(item) for item in payload]


def _cache_set(source_key: str, query: str, region: str, limit: int, items: list[ProductItem]) -> None:
    if not CACHE_ENABLED:
        return
    key = _cache_key(source_key, query, region, limit)
    _CACHE[key] = (
        time.monotonic() + CACHE_TTL_SECONDS,
        [_product_to_dict(item) for item in items],
    )


def _build_source_result(
    source_key: str,
    source_name: str,
    items: list[ProductItem],
    status: str,
    warning: Optional[str],
    cache_hit: bool,
) -> SearchResult:
    prices = [i.price for i in items if i.price and i.price > 0]
    return SearchResult(
        source=source_name,
        source_key=source_key,
        status=status,
        items=[_product_to_dict(i) for i in items],
        total_found=len(items),
        price_min=min(prices) if prices else None,
        price_max=max(prices) if prices else None,
        price_avg=round(sum(prices) / len(prices), 2) if prices else None,
        warning=warning,
        cache_hit=cache_hit,
    )


async def _run_parser_source(
    source_key: str,
    parser_cls: type,
    query: str,
    region: str,
    limit: int,
    variants: list[str],
) -> tuple[list[ProductItem], str, Optional[str], bool]:
    cached = _cache_get(source_key, query, region, limit)
    if cached is not None:
        return cached, "success", None, True

    async def _attempt(search_query: str) -> list[ProductItem]:
        async with parser_cls() as parser:
            return await parser.search(search_query, region, limit)

    try:
        items = await asyncio.wait_for(_attempt(query), timeout=PARSER_TIMEOUT_SECONDS)
        if not items and variants:
            retry_query = next((v for v in variants if v != query), None)
            if retry_query:
                items = await asyncio.wait_for(_attempt(retry_query), timeout=PARSER_TIMEOUT_SECONDS)
        items = items or []
        _cache_set(source_key, query, region, limit, items)
        status = "success" if items else "partial"
        warning = None if items else "Источник ответил, но товары не найдены"
        return items, status, warning, False
    except asyncio.TimeoutError:
        logger.warning("[%s] parser timeout", source_key)
        return [], "timeout", "Источник не успел ответить", False
    except Exception as exc:
        logger.exception("[%s] parser failed: %s", source_key, exc)
        return [], "failed", str(exc), False


async def _search_impl(q: str, region: str, limit: int) -> SearchResponse:
    started = time.perf_counter()
    nlp = process_query(q)
    primary = nlp["primary_query"]
    variants = nlp["search_variants"]
    logger.info("Search: '%s' -> '%s', region=%s", q, primary, region)

    runet_limit = min(limit, 8)
    parser_tasks = {
        "wildberries": _run_parser_source("wildberries", WildberriesParser, primary, region, limit, variants),
        "ozon": _run_parser_source("ozon", OzonParser, primary, region, limit, variants),
        "yandex_market": _run_parser_source("yandex_market", YandexMarketParser, primary, region, limit, variants),
        "runet": _run_parser_source("runet", RunetParser, primary, region, runet_limit, variants),
    }
    raw = await asyncio.gather(*parser_tasks.values())
    by_key = dict(zip(parser_tasks.keys(), raw))

    ranked: dict[str, list[ProductItem]] = {}
    for source_key, (items, _, _, _) in by_key.items():
        ranked[source_key] = await asyncio.get_event_loop().run_in_executor(
            None, rank_items, primary, items
        )

    results: list[SearchResult] = []
    for source_key, source_name in SOURCE_ORDER:
        _, status, warning, cache_hit = by_key[source_key]
        results.append(
            _build_source_result(
                source_key,
                source_name,
                ranked[source_key],
                status,
                warning,
                cache_hit,
            )
        )

    total = sum(r.total_found for r in results)
    warnings = [
        f"{r.source}: {r.warning}"
        for r in results
        if r.warning or r.status in {"failed", "timeout"}
    ]
    groups = {r.source_key: r.items for r in results}
    flat_items = [item for r in results for item in r.items]
    sources = [
        SourceSummary(
            source=r.source,
            source_key=r.source_key,
            status=r.status,
            count=r.total_found,
            min_price=r.price_min,
            max_price=r.price_max,
            avg_price=r.price_avg,
            cache_hit=r.cache_hit,
            warning=r.warning,
        )
        for r in results
    ]
    processing_time_ms = int((time.perf_counter() - started) * 1000)
    any_cache_hit = any(r.cache_hit for r in results)

    logger.info("Search done: %s items total in %sms", total, processing_time_ms)

    return SearchResponse(
        original_query=q,
        corrected_query=nlp["corrected"] if nlp["was_corrected"] else None,
        was_corrected=nlp["was_corrected"],
        search_variants=variants,
        used_synonyms=nlp["used_synonyms"],
        category=nlp["category"],
        region=region,
        results=results,
        total_items=total,
        query=QueryMeta(
            original=q,
            corrected=nlp["corrected"],
            expanded=variants,
            used_synonyms=nlp["used_synonyms"],
        ),
        items=flat_items,
        groups=groups,
        sources=sources,
        meta=ResponseMeta(
            original_query=q,
            corrected_query=nlp["corrected"],
            expanded_queries=variants,
            region=region,
            total_count=total,
            sources_summary=sources,
            processing_time_ms=processing_time_ms,
            warnings=warnings,
            cache_hit=any_cache_hit,
        ),
        cache_hit=any_cache_hit,
    )


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=2, description="Поисковый запрос"),
    region: str = Query("Москва", description="Регион пользователя"),
    limit: int = Query(10, ge=1, le=30, description="Количество результатов на источник"),
):
    return await _search_impl(q, region, limit)


@router.post("/search", response_model=SearchResponse)
async def search_post(payload: SearchRequest):
    limit = max(1, min(payload.limit_per_source, 30))
    return await _search_impl(payload.query, payload.region, limit)


@router.get("/search/suggest")
async def suggest(q: str = Query(..., min_length=1)):
    q_lower = q.lower().strip()
    suggestions = [k for k in SYNONYM_MAP.keys() if q_lower in k][:6]
    return {"suggestions": suggestions}
