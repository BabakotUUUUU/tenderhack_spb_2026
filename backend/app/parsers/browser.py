import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass, field

from app.parsers.common import detect_blocked_page
from app.parsers.http_client import USER_AGENTS

logger = logging.getLogger(__name__)

_playwright = None
_browser = None
_lock = asyncio.Lock()
_browser_semaphore = asyncio.Semaphore(int(os.getenv("BROWSER_CONCURRENCY", "1")))


@dataclass
class BrowserResult:
    html: str = ""
    json_payloads: list[dict | list] = field(default_factory=list)
    product_payloads: list[dict | list] = field(default_factory=list)
    status: str = "empty"
    errorReason: str = ""


async def get_browser():
    global _playwright, _browser
    async with _lock:
        if _browser and _browser.is_connected():
            return _browser
        from playwright.async_api import async_playwright
        _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-infobars",
            ],
        )
        return _browser


def _browser_proxy() -> dict | None:
    proxy = os.getenv("PROXY_URL")
    if not proxy:
        proxies = [p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()]
        proxy = random.choice(proxies) if proxies else ""
    return {"server": proxy} if proxy else None


def _looks_like_product_payload(data: dict | list) -> bool:
    text = json.dumps(data, ensure_ascii=False)[:250_000].lower()
    markers = ("product", "sku", "offer", "price", "товар", "model", "wareid", "cardprice", "nm_id", "market")
    return sum(1 for marker in markers if marker in text) >= 2


async def fetch_rendered_html(
    url: str,
    *,
    referer: str = "",
    region: str = "",
    wait_selectors: list[str] | None = None,
    scroll_steps: int = 3,
) -> BrowserResult:
    async with _browser_semaphore:
        browser = await get_browser()
        context = None
        page = None
        on_response = None
        payloads: list[dict | list] = []
        product_payloads: list[dict | list] = []
        try:
            proxy = _browser_proxy()
            context_kwargs = {
                "user_agent": random.choice(USER_AGENTS),
                "locale": "ru-RU",
                "timezone_id": "Europe/Moscow",
                "viewport": {"width": 1366, "height": 900},
                "extra_http_headers": {
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                    "Referer": referer,
                } if referer else {"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8"},
            }
            if proxy:
                context_kwargs["proxy"] = proxy
            context = await browser.new_context(
                **context_kwargs
            )
            context.set_default_timeout(8_000)
            context.set_default_navigation_timeout(15_000)
            await context.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US'] });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4] });
                window.chrome = window.chrome || { runtime: {} };
                """
            )
            page = await context.new_page()

            async def route_handler(route):
                try:
                    if route.request.resource_type in {"image", "media", "font"}:
                        await route.abort()
                    else:
                        await route.continue_()
                except Exception:
                    return

            await page.route("**/*", route_handler)

            async def on_response(response):
                ctype = response.headers.get("content-type", "")
                if "json" not in ctype:
                    return
                try:
                    data = await response.json()
                    if isinstance(data, (dict, list)):
                        payloads.append(data)
                        if _looks_like_product_payload(data):
                            product_payloads.append(data)
                except Exception:
                    return

            page.on("response", on_response)
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            except asyncio.CancelledError:
                logger.info("[Browser] fallback cancelled for %s", url)
                return BrowserResult(status="error", errorReason="browser fallback cancelled")
            selectors = wait_selectors or [
                'a[href*="/product/"]',
                'a[href*="/catalog/"]',
                '[data-zone-name*="product" i]',
                '[data-widget*="searchResults" i]',
                "article",
            ]
            for selector in selectors:
                try:
                    await page.wait_for_selector(selector, timeout=2_000)
                    break
                except asyncio.CancelledError:
                    return BrowserResult(status="error", errorReason="browser fallback cancelled")
                except Exception:
                    continue
            for _ in range(max(0, scroll_steps)):
                try:
                    await page.mouse.wheel(0, 1200)
                    await page.wait_for_timeout(500)
                except asyncio.CancelledError:
                    return BrowserResult(status="error", errorReason="browser fallback cancelled")
                except Exception:
                    break
            try:
                await page.wait_for_load_state("networkidle", timeout=3_000)
            except asyncio.CancelledError:
                return BrowserResult(status="error", errorReason="browser fallback cancelled")
            except Exception:
                pass
            html = await page.content()
            status_code = response.status if response else 0
            if detect_blocked_page(html, status_code):
                if product_payloads:
                    return BrowserResult(
                        html=html,
                        json_payloads=payloads,
                        product_payloads=product_payloads,
                        status="ok",
                        errorReason="Access page detected, but public product XHR payloads were captured",
                    )
                return BrowserResult(html=html, json_payloads=payloads, product_payloads=product_payloads, status="blocked", errorReason="CAPTCHA or access restriction")
            return BrowserResult(html=html, json_payloads=payloads, product_payloads=product_payloads, status="ok" if html else "empty")
        except asyncio.CancelledError:
            logger.info("[Browser] fallback cancelled for %s", url)
            return BrowserResult(status="error", errorReason="browser fallback cancelled")
        except Exception as exc:
            logger.info("[Browser] fallback failed for %s: %s", url, exc)
            return BrowserResult(status="error", errorReason=str(exc))
        finally:
            if page:
                if on_response:
                    try:
                        page.remove_listener("response", on_response)
                    except Exception:
                        pass
                try:
                    await page.close()
                except Exception:
                    pass
            if context:
                try:
                    await context.close()
                except Exception:
                    pass


async def new_page(context_options: dict | None = None):
    browser = await get_browser()
    context = await browser.new_context(**(context_options or {}))
    return await context.new_page()


async def close_browser() -> None:
    global _playwright, _browser
    if _browser:
        await _browser.close()
        _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None
