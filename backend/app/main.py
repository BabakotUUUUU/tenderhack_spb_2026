import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.search import router as search_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    try:
        from app.parsers.browser import close_browser
        await close_browser()
    except Exception:
        logger.info("[Main] browser shutdown skipped", exc_info=True)


app = FastAPI(
    title="TenderHack Price Search",
    description="Runtime price search in open sources without DB, auth, or external search APIs.",
    version="2.0.0",
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

