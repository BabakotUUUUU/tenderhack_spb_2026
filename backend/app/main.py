"""
TenderHack — Сервис поиска цен в открытых источниках
Главный модуль FastAPI
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.search import router as search_router
from app.api.health import router as health_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Прогрев ML-модели при старте — чтобы первый запрос был быстрым
    import asyncio
    loop = asyncio.get_event_loop()
    try:
        from app.ml.ranker import warmup
        await loop.run_in_executor(None, warmup)
    except Exception as exc:
        logger.warning(f"ML warmup skipped: {exc}")
    yield


app = FastAPI(
    title="TenderHack Price Search",
    description="Интеллектуальный сервис поиска цен в открытых источниках",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router, prefix="/api")
app.include_router(health_router, prefix="/api")


@app.get("/")
async def root():
    return {"status": "ok", "service": "TenderHack Price Search API"}
