"""
Парсер Ozon.

Метод: httpx → страница поиска ozon.ru/search/ → извлечение
embedded JSON из script-тегов (window.__NUXT__, application/json).
Дополнительно: DOM-парсинг через BeautifulSoup как fallback.

Не использует никаких API-эндпоинтов с ключами.
"""

import json
import logging
import re
from typing import Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from app.parsers.base import BaseParser, ProductItem
from app.parsers.http_client import Fetcher, browser_headers

logger = logging.getLogger(__name__)

_PRICE_RE = re.compile(r"(\d[\d\s ]{1,10})\s*₽")


def _price(value) -> Optional[float]:
    if value is None:
        return None
    text = str(value)
    m = _PRICE_RE.search(text)
    if m:
        text = m.group(1)
    text = re.sub(r"[^\d]", "", text)
    if not text:
        return None
    v = float(text)
    return v if 10 <= v <= 10_000_000 else None


class OzonParser(BaseParser):
    source_name = "Ozon"
    domain = "ozon.ru"

    async def search(self, query: str, region: str = "Москва", limit: int = 10) -> list[ProductItem]:
        headers = browser_headers("https://www.ozon.ru/")
        params  = {"text": query, "from_global": "true"}

        async with Fetcher(timeout=18.0) as f:
            html = await f.get_text(
                "https://www.ozon.ru/search/",
                headers=headers,
                params=params,
                retries=1,
            )

        if html:
            results = self._from_embedded(html, limit)
            if results:
                logger.info(f"[Ozon] embedded JSON: {len(results)} items")
                return results

            results = self._from_dom(html, limit)
            if results:
                logger.info(f"[Ozon] DOM: {len(results)} items")
                return results
        else:
            logger.warning(f"[Ozon] no HTML via httpx for '{query}', trying Playwright")

        results = await self._search_playwright(query, limit)
        if results:
            logger.info(f"[Ozon] Playwright: {len(results)} items")
            return results

        logger.warning(f"[Ozon] 0 items for '{query}'")
        return []

    # ── embedded JSON ─────────────────────────────────────────────────────────

    def _from_embedded(self, html: str, limit: int) -> list[ProductItem]:
        results: list[ProductItem] = []

        # Паттерн 1: application/json script-теги
        soup = BeautifulSoup(html[:800_000], "lxml")
        for tag in soup.find_all("script", {"type": "application/json"}):
            try:
                data = json.loads(tag.string or "")
                for node in self._walk(data):
                    p = self._from_node(node)
                    if p:
                        results.append(p)
                    if len(results) >= limit:
                        return _dedupe(results)
            except Exception:
                continue

        if results:
            return _dedupe(results)

        # Паттерн 2: window.__NUXT__ и похожие inline-переменные
        for m in re.finditer(
            r'(?:window\.__(?:NUXT|INITIAL_STATE|STATE)__\s*=\s*|"widgetStates"\s*:\s*)(\{.{100,}?\}(?=[,;\s]|$))',
            html[:1_000_000],
            re.DOTALL,
        ):
            try:
                data = json.loads(m.group(1))
                for node in self._walk(data):
                    p = self._from_node(node)
                    if p:
                        results.append(p)
                    if len(results) >= limit:
                        return _dedupe(results)
            except Exception:
                continue

        return _dedupe(results)

    def _walk(self, node, depth: int = 0):
        if depth > 14:
            return
        if isinstance(node, dict):
            if self._is_product(node):
                yield node
            for v in node.values():
                yield from self._walk(v, depth + 1)
        elif isinstance(node, list):
            for item in node[:200]:
                yield from self._walk(item, depth + 1)
        elif isinstance(node, str):
            text = node.strip()
            if 20 <= len(text) <= 200_000 and text[0] in "{[":
                try:
                    yield from self._walk(json.loads(text), depth + 1)
                except Exception:
                    return

    def _is_product(self, d: dict) -> bool:
        keys = set(d.keys())
        action = d.get("action")
        direct_link = d.get("link") or d.get("url") or (action.get("link") if isinstance(action, dict) else None)
        has_title = bool(keys & {"title", "name"}) or ("mainState" in keys and bool(self._text_candidates(d)))
        has_link = isinstance(direct_link, str) and ("/product/" in direct_link or "ozon.ru/product/" in direct_link)
        has_price = bool(keys & {"price", "finalPrice", "cardPrice", "priceWithCard"}) or ("mainState" in keys and bool(
            self._extract_price(d)
        ))
        return has_title and has_link and has_price

    def _from_node(self, d: dict) -> Optional[ProductItem]:
        title = self._extract_title(d)
        if not title or len(str(title)) < 3:
            return None

        link = self._extract_link(d)
        if not link:
            return None

        url = f"https://www.ozon.ru{link}" if link.startswith("/") else link

        price = self._extract_price(d)
        image_url = self._extract_image(d)

        chars: dict = {}
        brand = d.get("brand") or d.get("brandName")
        if brand:
            chars["Бренд"] = str(brand)

        return ProductItem(
            title=str(title).strip()[:300],
            price=price,
            id=self._extract_id(d),
            image_url=image_url,
            product_url=url,
            source=self.source_name,
            domain=self.domain,
            characteristics=chars,
        )

    def _extract_title(self, node: dict) -> Optional[str]:
        title = node.get("title") or node.get("name")
        if isinstance(title, dict):
            title = title.get("text") or title.get("content")
        if isinstance(title, str) and len(title.strip()) >= 3:
            return title.strip()

        for text in self._text_candidates(node):
            if "₽" not in text and len(text) >= 5:
                return text
        return None

    def _extract_link(self, node) -> Optional[str]:
        if isinstance(node, dict):
            action = node.get("action")
            for value in (node.get("link"), node.get("url"), action.get("link") if isinstance(action, dict) else None):
                if isinstance(value, str) and ("/product/" in value or "ozon.ru/product/" in value):
                    return value
            for value in node.values():
                found = self._extract_link(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node[:80]:
                found = self._extract_link(item)
                if found:
                    return found
        return None

    def _extract_price(self, node) -> Optional[float]:
        if isinstance(node, dict):
            for key in ("price", "finalPrice", "cardPrice", "priceWithCard"):
                price = _price(node.get(key))
                if price:
                    return price
            for value in node.values():
                price = self._extract_price(value)
                if price:
                    return price
        elif isinstance(node, list):
            for item in node[:100]:
                price = self._extract_price(item)
                if price:
                    return price
        elif isinstance(node, str):
            return _price(node)
        return None

    def _extract_image(self, node) -> Optional[str]:
        if isinstance(node, dict):
            for key in ("image", "imageUrl", "mainImage", "tileImage", "coverImage", "src"):
                val = node.get(key)
                if isinstance(val, str) and ("http" in val or val.startswith("//")):
                    return "https:" + val if val.startswith("//") else val
                if isinstance(val, dict):
                    nested = self._extract_image(val)
                    if nested:
                        return nested
            for value in node.values():
                found = self._extract_image(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node[:80]:
                found = self._extract_image(item)
                if found:
                    return found
        elif isinstance(node, str) and re.search(r"https?://.*\.(?:jpg|jpeg|png|webp)", node):
            return node
        return None

    def _extract_id(self, node: dict) -> Optional[str]:
        for key in ("id", "sku", "productId", "offerId"):
            value = node.get(key)
            if value:
                return str(value)
        link = self._extract_link(node) or ""
        match = re.search(r"-(\d+)/?\?", link) or re.search(r"/product/[^/]+-(\d+)/?", link)
        return match.group(1) if match else None

    def _text_candidates(self, node, depth: int = 0):
        if depth > 6:
            return []
        out: list[str] = []
        if isinstance(node, dict):
            for key in ("text", "content"):
                value = node.get(key)
                if isinstance(value, str):
                    clean = re.sub(r"\s+", " ", value).strip()
                    if clean:
                        out.append(clean)
            for value in node.values():
                out.extend(self._text_candidates(value, depth + 1))
        elif isinstance(node, list):
            for item in node[:80]:
                out.extend(self._text_candidates(item, depth + 1))
        return out

    # ── DOM fallback ──────────────────────────────────────────────────────────

    def _from_dom(self, html: str, limit: int) -> list[ProductItem]:
        soup = BeautifulSoup(html[:600_000], "lxml")
        results: list[ProductItem] = []

        for a in soup.select('a[href*="/product/"]'):
            href  = a.get("href", "")
            title = a.get_text(" ", strip=True)
            if not href or len(title) < 4:
                continue

            parent = a.find_parent("div") or a
            parent_text = parent.get_text(" ", strip=True)
            price = _price(parent_text)

            img = parent.select_one("img")
            image_url = img.get("src") if img else None

            url = f"https://www.ozon.ru{href}" if href.startswith("/") else href
            results.append(ProductItem(
                title=title[:300],
                price=price,
                image_url=image_url,
                product_url=url,
                source=self.source_name,
                domain=self.domain,
            ))
            if len(results) >= limit:
                break

        return _dedupe(results)

    async def _search_playwright(self, query: str, limit: int) -> list[ProductItem]:
        try:
            from app.parsers.browser import new_page
        except ImportError:
            return []

        page = None
        try:
            page = await new_page()
            url = f"https://www.ozon.ru/search/?text={quote_plus(query)}&from_global=true"
            await page.goto(url, wait_until="domcontentloaded", timeout=18_000)
            try:
                await page.wait_for_selector('a[href*="/product/"]', timeout=8_000)
            except Exception:
                pass
            html = await page.content()
            return self._from_embedded(html, limit) or self._from_dom(html, limit)
        except Exception as exc:
            logger.warning(f"[Ozon Playwright] {exc}")
            return []
        finally:
            if page:
                try:
                    await page.context.close()
                except Exception:
                    pass


def _dedupe(items: list[ProductItem]) -> list[ProductItem]:
    seen: set[str] = set()
    out: list[ProductItem] = []
    for item in items:
        key = item.product_url.split("?")[0]
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
