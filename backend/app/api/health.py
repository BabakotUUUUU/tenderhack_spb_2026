"""Health check endpoint."""

import os
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    cache_enabled = os.getenv("CACHE_ENABLED", "true").lower() not in {"0", "false", "no"}

    try:
        from app.search_index.db import count_indexed, get_connection, DB_PATH
        conn = get_connection(DB_PATH)
        indexed_count = count_indexed(conn)
        conn.close()
        index_status = f"ok ({indexed_count} pages indexed)"
    except Exception:
        index_status = "not initialized"

    return {
        "status": "ok",
        "services": {
            "backend": "ok",
            "runet_index": index_status,
            "cache": "ok" if cache_enabled else "disabled",
            "ranking": "lexical (token overlap + quality score)",
        },
    }
