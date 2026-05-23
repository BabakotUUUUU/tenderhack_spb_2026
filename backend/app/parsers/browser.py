"""
Менеджер Playwright-браузера.

Singleton: один экземпляр Chromium на весь процесс.
Stealth-режим: скрывает признаки автоматизации от антибот-систем.
"""

import asyncio
import logging

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
    "--disable-extensions",
    "--disable-default-apps",
    # Скрываем признаки автоматизации
    "--disable-blink-features=AutomationControlled",
    "--disable-web-security",
    "--disable-features=IsolateOrigins,site-per-process",
    "--allow-running-insecure-content",
    "--disable-infobars",
    # Производительность в Docker без GPU
    "--disable-accelerated-2d-canvas",
    "--disable-canvas-aa",
    "--disable-3d-apis",
    "--disable-partial-raster",
    "--use-gl=swiftshader",
    "--disable-software-rasterizer",
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# JS-скрипт: скрываем webdriver flag от сайтов
_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US'] });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'permissions', {
  get: () => ({ query: () => Promise.resolve({ state: 'granted' }) })
});
"""


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
            logger.info("[Browser] Chromium launched (stealth mode)")
        except Exception as exc:
            logger.error(f"[Browser] Launch failed: {exc}")
            _browser = None
            raise

    return _browser


async def new_page(context_options: dict = None):
    """
    Создаёт новую страницу с реалистичными параметрами.
    Вызывающий код должен закрыть page.context после использования.
    """
    browser = await get_browser()
    opts = {
        "user_agent": UA,
        "locale": "ru-RU",
        "timezone_id": "Europe/Moscow",
        "viewport": {"width": 1440, "height": 900},
        "extra_http_headers": {
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Upgrade-Insecure-Requests": "1",
        },
    }
    if context_options:
        opts.update(context_options)

    context = await browser.new_context(**opts)
    # Стелс-скрипт применяется к каждой новой странице
    await context.add_init_script(_STEALTH_SCRIPT)
    page = await context.new_page()
    return page


async def close_browser():
    """Закрывает браузер при shutdown приложения."""
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
