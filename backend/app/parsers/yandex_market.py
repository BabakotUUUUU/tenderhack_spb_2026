"""
Парсер Яндекс Маркет.

Стратегии:
  1. httpx → market.yandex.ru/search → __NEXT_DATA__ JSON
  2. httpx → DOM-парсинг через BeautifulSoup
  3. Playwright fallback (если httpx заблокирован)
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

REGION_IDS: dict[str, int] = {
    "москва":          213,
    "санкт-петербург": 2,
    "спб":             2,
    "новосибирск":     65,
    "екатеринбург":    54,
    "казань":          43,
    "нижний новгород": 47,
    "краснодар":       35,
    "ростов-на-дону":  39,
    "уфа":             172,
    "самара":          51,
    "омск":            66,
    "default":         213,
}


def _rid(region: str) -> int:
    return REGION_IDS.get(region.lower().strip(), REGION_IDS["default"])


def _price(value) -> Optional[float]:
    if value is None:
        return None
    text = re.sub(r"[^\d]", "", str(value))
    if not text:
        return None
    v = float(text)
    return v if 10 <= v <= 10_000_000 else None


class YandexMarketParser(BaseParser):
    source_name = "Яндекс Маркет"
    domain = "market.yandex.ru"

    async def search(self, query: str, region: str = "Москва", limit: int = 10) -> list[ProductItem]:
        rid = _rid(region)

        # Попытка 1: httpx + __NEXT_DATA__
        results = await self._search_httpx(query, rid, limit)
        if results:
            return results[:limit]

        # Попытка 2: Playwright (если httpx заблокирован)
        results = await self._search_playwright(query, rid, limit)
        return results[:limit]

    # ── httpx ────────────────────────────────────────────────────────────────

    async def _search_httpx(self, query: str, rid: int, limit: int) -> list[ProductItem]:
        headers = browser_headers("https://market.yandex.ru/")
        headers["Cookie"] = f"_region_id={rid}; yandex_gid={rid}; my=YycCAAA=;"

        async with Fetcher(timeout=16.0) as f:
            html = await f.get_text(
                "https://market.yandex.ru/search",
                params={"text": query, "lr": rid},
                headers=headers,
                retries=1,
            )

        if not html:
            return []

        results = self._from_next_data(html, limit)
        if results:
            logger.info(f"[YM] __NEXT_DATA__: {len(results)} items")
            return results

        results = self._from_dom(html, limit)
        if results:
            logger.info(f"[YM] DOM: {len(results)} items")
        return results

    # ── __NEXT_DATA__ ─────────────────────────────────────────────────────────

    def _from_next_data(self, html: str, limit: int) -> list[ProductItem]:
        m = re.search(
            r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html, re.DOTALL,
        )
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
        except Exception:
            return []

        results: list[ProductItem] = []
        for node in self._walk(data):
            p = self._from_node(node)
            if p:
                results.append(p)
            if len(results) >= limit:
                break
        return _dedupe(results)

    def _walk(self, node, depth: int = 0):
        if depth > 15:
            return
        if isinstance(node, dict):
            if self._is_product(node):
                yield node
                return
            for v in node.values():
                yield from self._walk(v, depth + 1)
        elif isinstance(node, list):
            for item in node[:300]:
                yield from self._walk(item, depth + 1)

    def _is_product(self, d: dict) -> bool:
        keys = set(d.keys())
        has_name  = bool(keys & {"title", "name", "modelName"})
        has_price = bool(keys & {"price", "priceValue", "offers", "wareId"})
        has_id    = bool(keys & {"id", "offerId", "modelId", "skuId", "slug", "wareId"})
        return has_name and (has_price or has_id)

    def _from_node(self, d: dict) -> Optional[ProductItem]:
        title = d.get("title") or d.get("name") or d.get("modelName")
        if not title or len(str(title)) < 3:
            return None

        price = _price(d.get("price") or d.get("priceValue"))
        if not price:
            offers = d.get("offers")
            if isinstance(offers, list) and offers:
                price = _price(offers[0].get("price") or offers[0].get("priceValue"))
            elif isinstance(offers, dict):
                price = _price(offers.get("price") or offers.get("min"))

        url = d.get("url") or d.get("productUrl")
        if not url:
            pid = d.get("id") or d.get("modelId") or d.get("skuId") or d.get("slug")
            url = f"https://market.yandex.ru/product/{pid}" if pid else None
        if not url:
            return None
        if url.startswith("/"):
            url = "https://market.yandex.ru" + url

        image = d.get("picture") or d.get("image") or d.get("imageUrl") or d.get("thumbnail")
        if isinstance(image, dict):
            image = image.get("url") or image.get("src")
        if image and isinstance(image, str) and image.startswith("//"):
            image = "https:" + image

        chars: dict = {}
        brand = d.get("brand") or (d.get("vendor") or {}).get("name") if isinstance(d.get("vendor"), dict) else d.get("brand")
        if brand:
            chars["Бренд"] = str(brand)

        rating = d.get("rating") or (d.get("ratings") or {}).get("value") if isinstance(d.get("ratings"), dict) else d.get("rating")
        reviews = d.get("reviewCount") or d.get("opinionsCount") or d.get("feedbackCount")

        product_id = d.get("id") or d.get("modelId") or d.get("skuId") or d.get("wareId")

        return ProductItem(
            title=str(title).strip()[:300],
            price=price,
            id=str(product_id) if product_id else None,
            image_url=image,
            product_url=url,
            source=self.source_name,
            domain=self.domain,
            characteristics=chars,
            rating=float(rating) if rating else None,
            reviews_count=int(reviews) if reviews else None,
        )

    # ── DOM fallback ──────────────────────────────────────────────────────────

    def _from_dom(self, html: str, limit: int) -> list[ProductItem]:
        soup = BeautifulSoup(html[:600_000], "lxml")
        results: list[ProductItem] = []

        candidates = (
            soup.find_all(attrs={"data-zone-name": "productSnippet"})
            or soup.find_all(attrs={"data-autotest-id": re.compile(r"product", re.I)})
            or soup.find_all("article")
        )

        for card in candidates[:limit * 2]:
            try:
                title_el = (
                    card.find(attrs={"data-autotest-id": "product-name"})
                    or card.find("h3") or card.find("h2")
                )
                title = title_el.get_text(strip=True) if title_el else ""
                if not title or len(title) < 3:
                    continue

                price_el = card.find(class_=re.compile(r"price", re.I))
                price = _price(re.sub(r"[^\d]", "", price_el.get_text()) if price_el else None)

                link_el = card.find("a", href=True)
                href = link_el["href"] if link_el else ""
                url = href if href.startswith("http") else f"https://market.yandex.ru{href}"

                img_el = card.find("img")
                image_url = None
                if img_el:
                    src = img_el.get("src") or img_el.get("data-src", "")
                    if src and src.startswith("//"):
                        src = "https:" + src
                    image_url = src or None

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
            except Exception:
                continue

        return _dedupe(results)

    # ── Playwright fallback ───────────────────────────────────────────────────

    async def _search_playwright(self, query: str, rid: int, limit: int) -> list[ProductItem]:
        try:
            from app.parsers.browser import new_page
        except ImportError:
            return []

        page = None
        try:
            page = await new_page()
            await page.context.add_cookies([
                {"name": "_region_id", "value": str(rid),
                 "domain": ".market.yandex.ru", "path": "/"},
            ])
            url = f"https://market.yandex.ru/search?text={quote_plus(query)}&lr={rid}"
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            try:
                await page.wait_for_selector(
                    "article, [data-zone-name='productSnippet']",
                    timeout=8_000,
                )
            except Exception:
                pass
            html = await page.content()
            results = self._from_next_data(html, limit) or self._from_dom(html, limit)
            if results:
                logger.info(f"[YM] Playwright: {len(results)} items")
            return results
        except Exception as exc:
            logger.warning(f"[YM Playwright] {exc}")
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
        key = item.product_url.split("?")[0].rstrip("/")
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
