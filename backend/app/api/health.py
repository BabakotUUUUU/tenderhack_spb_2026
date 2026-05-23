from fastapi import APIRouter

from app.parsers.service import parsers_health

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "service": "TenderHack Price Search API"}


@router.get("/parsers/health")
async def parser_health():
    return {"sources": parsers_health()}

