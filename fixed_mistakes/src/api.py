"""
Простой FastAPI сервис для удалённого доступа к spell-checker.

Запуск:
    uvicorn src.api:app --host 0.0.0.0 --port 8000

Endpoints:
    POST /correct        -> одиночная коррекция
    POST /correct/topk   -> топ-k вариантов
    POST /correct/batch  -> пакетная коррекция (async)
    GET  /health         -> проверка состояния
    POST /dict/import    -> импорт словаря
    GET  /dict/export    -> экспорт словаря
"""

from __future__ import annotations

import os
import asyncio
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

# Ленивая инициализация checker в lifespan
checker = None


class CorrectRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=512, description="Исходный запрос")
    max_length: int = Field(default=128, ge=16, le=512)


class CorrectTopKRequest(CorrectRequest):
    k: int = Field(default=3, ge=1, le=10)


class BatchRequest(BaseModel):
    texts: List[str] = Field(..., min_items=1, max_items=128, description="Список запросов")
    max_length: int = Field(default=128, ge=16, le=512)
    k: int = Field(default=1, ge=1, le=10)


class DictImportRequest(BaseModel):
    path: str = Field(..., description="Путь к файлу словаря (на сервере)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global checker
    # startup
    try:
        from src.spell_checker import RussianSpellChecker
        use_gpu = os.environ.get("SPELL_GPU", "false").lower() in ("1", "true", "yes")
        checker = RussianSpellChecker(use_gpu=use_gpu, fallback_to_dict=True)
    except Exception as exc:
        raise RuntimeError(f"Не удалось инициализировать spell checker: {exc}")
    yield
    # shutdown
    checker = None


app = FastAPI(
    title="Russian Spell Corrector API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.post("/correct", tags=["correction"])
async def correct(req: CorrectRequest):
    """Одиночная коррекция. Возвращает исходный и исправленный вариант."""
    if checker is None:
        raise HTTPException(status_code=503, detail="Service unavailable")
    result = checker.correct(req.text, max_length=req.max_length)
    return {"original": req.text, "results": result}


@app.post("/correct/topk", tags=["correction"])
async def correct_topk(req: CorrectTopKRequest):
    """Возвращает топ-k наиболее вероятных исправлений."""
    if checker is None:
        raise HTTPException(status_code=503, detail="Service unavailable")
    results = checker.correct_topk(req.text, k=req.k, max_length=req.max_length)
    return {"original": req.text, "results": results}


@app.post("/correct/batch", tags=["correction"])
async def correct_batch(req: BatchRequest):
    """Пакетная асинхронная коррекция."""
    if checker is None:
        raise HTTPException(status_code=503, detail="Service unavailable")
    results = await checker.correct_batch_async(
        req.texts, max_length=req.max_length, k=req.k
    )
    return {
        "originals": req.texts,
        "results": results,
    }


@app.get("/health", tags=["system"])
async def health():
    """Health-check."""
    return {
        "status": "ok" if checker is not None else "not_ready",
        "model": checker.current_model_name if checker else None,
        "dict_size": len(checker.hot_dict.get_words()) if checker else 0,
    }


@app.post("/dict/import", tags=["dictionary"])
async def dict_import(req: DictImportRequest):
    """Импортировать пользовательский словарь."""
    if checker is None:
        raise HTTPException(status_code=503, detail="Service unavailable")
    if not os.path.exists(req.path):
        raise HTTPException(status_code=400, detail=f"File not found: {req.path}")
    try:
        checker.import_dictionary(req.path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"status": "imported", "dict_size": len(checker.hot_dict.get_words())}


@app.get("/dict/export", tags=["dictionary"])
async def dict_export(path: Optional[str] = "data/exported_dict.txt"):
    """Экспортировать текущий словарь в файл."""
    if checker is None:
        raise HTTPException(status_code=503, detail="Service unavailable")
    try:
        checker.export_dictionary(path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"status": "exported", "path": path, "dict_size": len(checker.hot_dict.get_words())}
