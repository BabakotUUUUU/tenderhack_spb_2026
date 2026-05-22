"""
TenderHack — Сервис поиска цен в открытых источниках
Главный модуль FastAPI
"""

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncio
import logging
from typing import Optional

from app.api.search import router as search_router
from app.api.health import router as health_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TenderHack Price Search",
    description="Интеллектуальный сервис поиска цен в открытых источниках",
    version="1.0.0",
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
