from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.parsers.query_normalizer import SYNONYMS
from app.parsers.service import search_products

router = APIRouter()


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2)
    category: str = "tires"
    region: str = "Москва"
    limit: int = Field(10, ge=1, le=30)


@router.post("/search")
async def search_post(payload: SearchRequest):
    return await search_products(payload.query, payload.category, payload.region, payload.limit)


@router.get("/search")
async def search_get(
    q: str = Query(..., min_length=2),
    category: str = Query("tires"),
    region: str = Query("Москва"),
    limit: int = Query(10, ge=1, le=30),
):
    return await search_products(q, category, region, limit)


@router.get("/search/suggest")
async def suggest(q: str = Query(..., min_length=1)):
    needle = q.lower().strip()
    suggestions = [key for key in SYNONYMS if needle in key][:8]
    return {"suggestions": suggestions}

