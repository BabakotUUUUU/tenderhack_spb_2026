from fastapi import APIRouter

from app.parsers.service import parsers_health
from app.parsers.service import search_products

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "TenderHack Price Search API"}


@router.get("/parsers/health")
async def parser_health():
    return {"sources": parsers_health()}


@router.get("/parsers/smoke")
async def parser_smoke(q: str = "шины 205 55 r16", category: str = "tires", region: str = "Москва"):
    result = await search_products(q, category, region, 2)
    return {
        "query": result["query"],
        "normalizedQuery": result["normalizedQuery"],
        "summary": result["summary"],
        "sources": {
            source: {
                "status": group["status"],
                "count": group["count"],
                "errorReason": group["errorReason"],
                "diagnostics": group.get("diagnostics", {}),
            }
            for source, group in result["groups"].items()
        },
    }
