"""
TenderHack SPB 2026 — Сервис поиска цен в открытых источниках.
Главный модуль FastAPI с lifespan (startup / shutdown).
"""

import asyncio
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
    # ── Инициализация SQLite-индекса для Рунет-краулера ──────────────────
    try:
        from app.search_index.db import init_db, DB_PATH
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, init_db, DB_PATH)
        logger.info(f"[Main] Runet index ready at {DB_PATH}")
    except Exception as exc:
        logger.warning(f"[Main] Index init failed: {exc}")

    # Фоновая индексация отключена: она конкурировала с live-краулером за
    # соединения к 4tochki.ru/foroffice.ru и приводила к таймаутам поиска.
    # Индекс заполняется инкрементально через результаты пользовательских поисков.
    bg_task = None

    yield

    # ── Graceful shutdown ─────────────────────────────────────────────────
    if bg_task and not bg_task.done():
        bg_task.cancel()
        try:
            await bg_task
        except asyncio.CancelledError:
            pass

    try:
        from app.parsers.browser import close_browser
        await close_browser()
    except Exception:
        pass


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
