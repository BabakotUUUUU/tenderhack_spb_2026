"""
Менеджер Playwright-браузера.

Singleton: один экземпляр Chromium на весь процесс.
Используется парсерами Ozon и Яндекс Маркет для рендеринга JavaScript.

Зачем Playwright:
  Ozon и ЯМ рендерят контент через JavaScript (React/Next.js).
  Обычный HTTP-запрос получает пустую страницу или заглушку.
  Playwright запускает настоящий Chromium, который выполняет JS
  и возвращает полную DOM-структуру с реальными данными.
"""

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_browser = None
_playwright_ctx = None
_lock = asyncio.Lock()

BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-setuid-sandbox",
    "--no-first-run",
    "--no-zygote",
    "--single-process",
    "--disable-extensions",
]

# User-agent реального браузера
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def get_browser():
    """Возвращает singleton Playwright Browser (Chromium headless)."""
    global _browser, _playwright_ctx

    async with _lock:
        if _browser is not None and _browser.is_connected():
            return _browser

        try:
            from playwright.async_api import async_playwright
            _playwright_ctx = await async_playwright().start()
            _browser = await _playwright_ctx.chromium.launch(
                headless=True,
                args=BROWSER_ARGS,
            )
            logger.info("[Browser] Chromium launched")
        except Exception as exc:
            logger.error(f"[Browser] Launch failed: {exc}")
            _browser = None
            raise

    return _browser


async def new_page(context_options: dict = None):
    """
    Создаёт новую страницу с реалистичными параметрами браузера.
    Вызывающий код должен закрыть page.context и page после использования.
    """
    browser = await get_browser()
    opts = {
        "user_agent": UA,
        "locale": "ru-RU",
        "timezone_id": "Europe/Moscow",
        "viewport": {"width": 1366, "height": 768},
        "extra_http_headers": {
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        },
    }
    if context_options:
        opts.update(context_options)

    context = await browser.new_context(**opts)
    page = await context.new_page()
    return page


async def close_browser():
    """Закрывает браузер и Playwright. Вызывается при shutdown приложения."""
    global _browser, _playwright_ctx
    if _browser:
        try:
            await _browser.close()
        except Exception:
            pass
        _browser = None
    if _playwright_ctx:
        try:
            await _playwright_ctx.stop()
        except Exception:
            pass
        _playwright_ctx = None
    logger.info("[Browser] Closed")
