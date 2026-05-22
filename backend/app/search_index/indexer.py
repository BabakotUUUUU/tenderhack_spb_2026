"""
Индексирует продукты из ExtractedProduct в SQLite.
"""

import logging
import sqlite3
from typing import Optional

from app.crawler.extractor import ExtractedProduct
from app.search_index.db import DB_PATH, get_connection, init_db, upsert_page

logger = logging.getLogger(__name__)

_conn: Optional[sqlite3.Connection] = None


def get_index_connection() -> sqlite3.Connection:
    """Возвращает переиспользуемое подключение к индексу."""
    global _conn
    if _conn is None:
        init_db(DB_PATH)
        _conn = get_connection(DB_PATH)
    return _conn


def index_product(product: ExtractedProduct, category: str = "general") -> bool:
    """
    Сохраняет извлечённый товар в SQLite индекс.
    Возвращает True если товар добавлен/обновлён.
    """
    if not product.title or not product.url:
        return False

    # Минимальная фильтрация мусора
    if len(product.title) < 4:
        return False
    if product.price is not None and (product.price < 10 or product.price > 10_000_000):
        return False

    conn = get_index_connection()
    page = {
        "url": product.url,
        "domain": product.domain or "",
        "title": product.title[:500],
        "price": product.price,
        "old_price": product.old_price,
        "image_url": product.image_url,
        "description": (product.description or "")[:1000],
        "characteristics": product.characteristics or {},
        "category": product.category or category,
        "brand": product.brand,
        "source_label": f"Рунет ({product.domain})",
    }
    return upsert_page(conn, page)
