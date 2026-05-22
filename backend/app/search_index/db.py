"""
SQLite-хранилище с FTS5 для BM25-поиска товаров из Рунета.

Схема:
  pages     — основные данные товаров
  pages_fts — виртуальная FTS5-таблица для BM25-поиска
  crawl_log — лог выполненных краулов

SQLite FTS5 имеет встроенный BM25 через функцию bm25().
"""

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from typing import Generator, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("CRAWLER_DB_PATH", "/tmp/runet_index.db")


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Создаёт подключение к SQLite с нужными настройками."""
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=10000")
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    """Создаёт таблицы если их нет."""
    conn = get_connection(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pages (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                url                 TEXT    UNIQUE NOT NULL,
                domain              TEXT    NOT NULL,
                title               TEXT    NOT NULL,
                price               REAL,
                old_price           REAL,
                image_url           TEXT,
                description         TEXT,
                characteristics_json TEXT,
                category            TEXT,
                brand               TEXT,
                fetched_at          TEXT    DEFAULT (datetime('now')),
                source_label        TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_pages_domain   ON pages(domain);
            CREATE INDEX IF NOT EXISTS idx_pages_category ON pages(category);
            CREATE INDEX IF NOT EXISTS idx_pages_price    ON pages(price);

            CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                title,
                description,
                brand,
                category,
                domain,
                content='pages',
                content_rowid='id',
                tokenize='unicode61 remove_diacritics 1'
            );

            CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
                INSERT INTO pages_fts(rowid, title, description, brand, category, domain)
                VALUES (new.id, new.title, new.description, new.brand, new.category, new.domain);
            END;

            CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
                INSERT INTO pages_fts(pages_fts, rowid, title, description, brand, category, domain)
                VALUES ('delete', old.id, old.title, old.description, old.brand, old.category, old.domain);
                INSERT INTO pages_fts(rowid, title, description, brand, category, domain)
                VALUES (new.id, new.title, new.description, new.brand, new.category, new.domain);
            END;

            CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
                INSERT INTO pages_fts(pages_fts, rowid, title, description, brand, category, domain)
                VALUES ('delete', old.id, old.title, old.description, old.brand, old.category, old.domain);
            END;

            CREATE TABLE IF NOT EXISTS crawl_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                query      TEXT,
                category   TEXT,
                domain     TEXT,
                pages_found INTEGER,
                crawled_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()
        logger.info(f"[DB] Initialized at {db_path}")
    finally:
        conn.close()


def upsert_page(conn: sqlite3.Connection, page: dict) -> bool:
    """Вставляет или обновляет страницу. Возвращает True если вставлена."""
    try:
        cursor = conn.execute(
            """
            INSERT INTO pages
                (url, domain, title, price, old_price, image_url, description,
                 characteristics_json, category, brand, source_label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                title        = excluded.title,
                price        = excluded.price,
                old_price    = excluded.old_price,
                image_url    = excluded.image_url,
                description  = excluded.description,
                characteristics_json = excluded.characteristics_json,
                category     = excluded.category,
                brand        = excluded.brand,
                source_label = excluded.source_label,
                fetched_at   = datetime('now')
            """,
            (
                page["url"],
                page["domain"],
                page["title"],
                page.get("price"),
                page.get("old_price"),
                page.get("image_url"),
                page.get("description"),
                json.dumps(page.get("characteristics") or {}, ensure_ascii=False),
                page.get("category"),
                page.get("brand"),
                page.get("source_label"),
            ),
        )
        conn.commit()
        return cursor.lastrowid is not None
    except Exception as exc:
        logger.error(f"[DB] upsert error: {exc}")
        return False


def search_fts(
    conn: sqlite3.Connection,
    query: str,
    category: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """
    BM25-поиск через SQLite FTS5.
    Возвращает список словарей с данными товаров.
    """
    if not query.strip():
        return []

    # Нормализуем запрос для FTS5 (кавычим каждое слово)
    fts_query = " ".join(
        f'"{w}"'
        for w in query.lower().split()
        if len(w) > 1
    )
    if not fts_query:
        return []

    try:
        if category and category != "general":
            rows = conn.execute(
                """
                SELECT p.*, bm25(pages_fts) as bm25_score
                FROM pages_fts
                JOIN pages p ON p.id = pages_fts.rowid
                WHERE pages_fts MATCH ?
                  AND p.category = ?
                ORDER BY bm25_score
                LIMIT ?
                """,
                (fts_query, category, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT p.*, bm25(pages_fts) as bm25_score
                FROM pages_fts
                JOIN pages p ON p.id = pages_fts.rowid
                WHERE pages_fts MATCH ?
                ORDER BY bm25_score
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()

        return [dict(row) for row in rows]

    except sqlite3.OperationalError as exc:
        # FTS query syntax error → fallback to LIKE
        logger.warning(f"[DB] FTS error, fallback to LIKE: {exc}")
        return _search_like(conn, query, limit)


def _search_like(conn: sqlite3.Connection, query: str, limit: int) -> list[dict]:
    """Простой LIKE-поиск как fallback."""
    words = [f"%{w}%" for w in query.split() if len(w) > 2]
    if not words:
        return []
    conditions = " AND ".join(["(title LIKE ? OR description LIKE ?)"] * len(words))
    params = [p for w in words for p in (w, w)]
    rows = conn.execute(
        f"SELECT * FROM pages WHERE {conditions} LIMIT ?",
        params + [limit],
    ).fetchall()
    return [dict(r) for r in rows]


def count_indexed(conn: sqlite3.Connection) -> int:
    """Возвращает количество проиндексированных страниц."""
    return conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
