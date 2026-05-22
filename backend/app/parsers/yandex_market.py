"""
Парсер Яндекс Маркет.

Архитектура (двухуровневая):
  Уровень 1 — Playwright (Chromium headless):
    YM рендерится через Next.js (SSR + client hydration).
    Playwright загружает страницу полностью, включая JS-гидратацию,
    и предоставляет реальный DOM с карточками товаров.
    Регион устанавливается через Cookie lr={region_id}.

  Уровень 2 — httpx fallback:
    Парсим __NEXT_DATA__ из HTML (серверный рендеринг Next.js).
    Работает пока YM не заблокировал IP. Fallback на BS4.

Регионализация:
  Wildberries: параметр dest → точная региональная цена
  Яндекс Маркет: параметр lr + Cookie _region_id → фильтр склада/цены
"""

import asyncio
import json
import logging
import random
import re
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from app.parsers.base import BaseParser, ProductItem, get_headers

logger = logging.getLogger(__name__)

REGION_IDS: dict[str, int] = {
    "москва": 213,
    "санкт-петербург": 2,
    "спб": 2,
    "новосибирск": 65,
    "екатеринбург": 54,
    "казань": 43,
    "нижний новгород": 47,
    "краснодар": 35,
    "ростов-на-дону": 39,
    "уфа": 172,
    "самара": 51,
    "омск": 66,
    "default": 213,
}


def _get_rid(region: str) -> int:
    return REGION_IDS.get(region.lower().strip(), REGION_IDS["default"])


def _clean_price(raw) -> Optional[float]:
    if raw is None:
        return None
    s = re.sub(r"[^\d.]", "", str(raw).replace(",", ".").replace(" ", ""))
    try:
        v = float(s)
        return v if 10 <= v <= 10_000_000 else None
    except Exception:
        return None


class YandexMarketParser(BaseParser):
    source_name = "Яндекс Маркет"
    domain = "market.yandex.ru"

    async def search(self, query: str, region: str = "Москва", limit: int = 10) -> list[ProductItem]:
        rid = _get_rid(region)

        # Попытка 1: Playwright
        results = await self._search_playwright(query, rid, limit)
        if results:
            return results[:limit]

        # Попытка 2: httpx + HTML-парсинг
        results = await self._search_httpx(query, rid, limit)
        return results[:limit]

    # ------------------------------------------------------------------
    # Playwright — основной метод
    # ------------------------------------------------------------------
    async def _search_playwright(self, query: str, rid: int, limit: int) -> list[ProductItem]:
        try:
            from app.parsers.browser import new_page
        except ImportError:
            return []

        page = None
        try:
            page = await new_page(context_options={
                "extra_http_headers": {"Accept-Language": "ru-RU,ru;q=0.9"},
            })

            # Устанавливаем Cookie с регионом перед переходом
            await page.context.add_cookies([
                {"name": "_region_id", "value": str(rid),
                 "domain": ".market.yandex.ru", "path": "/"},
                {"name": "yandexuid", "value": "0",
                 "domain": ".yandex.ru", "path": "/"},
            ])

            url = f"https://market.yandex.ru/search?text={quote(query)}&lr={rid}"
            await page.goto(url, wait_until="domcontentloaded", timeout=25_000)

            # Ждём карточки товаров
            try:
                await page.wait_for_selector(
                    "article, "
                    "[data-zone-name='productSnippet'], "
                    "[data-autotest-id='product-snippet']",
                    timeout=12_000,
                )
            except Exception:
                pass

            await asyncio.sleep(random.uniform(1.5, 2.5))
            html = await page.content()

            return self._parse_html(html, limit)

        except Exception as exc:
            logger.warning(f"[YM Playwright] failed: {exc}")
            return []
        finally:
            if page:
                try:
                    await page.context.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # httpx fallback
    # ------------------------------------------------------------------
    async def _search_httpx(self, query: str, rid: int, limit: int) -> list[ProductItem]:
        headers = get_headers(f"https://market.yandex.ru/")
        headers["Cookie"] = (
            f"_region_id={rid}; yandexuid=0; i=0; skid=0; "
            f"my=YycCAAA=; L=AAAAAAAA; yuidss=0"
        )

        for url in [
            f"https://market.yandex.ru/search?text={quote(query)}&lr={rid}&clid=521",
            f"https://market.yandex.ru/search?text={quote(query)}&lr={rid}&pp=18",
        ]:
            try:
                await asyncio.sleep(random.uniform(2.5, 4.5))
                resp = await self.client.get(url, headers=headers,
                                             follow_redirects=True, timeout=25.0)
                if resp.status_code == 200:
                    results = self._parse_html(resp.text, limit)
                    if results:
                        logger.info(f"[YM httpx] {len(results)} items")
                        return results
            except Exception as exc:
                logger.debug(f"[YM httpx] {exc}")

        return []

    # ------------------------------------------------------------------
    # Разбор HTML (Playwright или httpx)
    # ------------------------------------------------------------------
    def _parse_html(self, html: str, limit: int) -> list[ProductItem]:
        if len(html) > 2_000_000:
            html = html[:2_000_000]

        # 1. __NEXT_DATA__ (Next.js — самый надёжный источник данных)
        results = self._extract_next_data(html, limit)
        if results:
            logger.info(f"[YM] __NEXT_DATA__: {len(results)} items")
            return results

        # 2. Regex-паттерны в inline-скриптах
        for pat in [
            r'window\.__NUXT__\s*=\s*\(function[^;]+\)',
            r'"offers"\s*:\s*(\[.+?\])',
            r'"searchResults"\s*:\s*(\{.+?\})',
        ]:
            for m in re.finditer(pat, html, re.DOTALL):
                try:
                    data = json.loads(m.group(1) if m.lastindex else m.group(0))
                    items = list(self._walk_json(data, limit))
                    if items:
                        return items
                except Exception:
                    continue

        # 3. BS4 DOM-парсинг (работает после Playwright-рендеринга)
        return self._parse_dom(html, limit)

    def _extract_next_data(self, html: str, limit: int) -> list[ProductItem]:
        m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.+?)</script>', html, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
            return list(self._walk_json(data, limit))
        except Exception:
            return []

    def _walk_json(self, node, limit: int, depth: int = 0):
        if depth > 15 or limit <= 0:
            return
        if isinstance(node, dict):
            if self._is_product(node):
                p = self._parse_product(node)
                if p:
                    yield p
                    return
            for v in node.values():
                yield from self._walk_json(v, limit, depth + 1)
        elif isinstance(node, list):
            for elem in node[:limit * 5]:
                yield from self._walk_json(elem, limit, depth + 1)

    def _is_product(self, d: dict) -> bool:
        keys = set(d.keys())
        has_name = bool(keys & {"name", "title", "modelName"})
        has_price = bool(keys & {"price", "prices", "offer", "minPrice"})
        has_id = bool(keys & {"id", "offerId", "modelId", "skuId", "entity"})
        return has_name and has_price and has_id

    def _parse_product(self, d: dict) -> Optional[ProductItem]:
        try:
            title = d.get("name") or d.get("title") or d.get("modelName") or ""
            if not title or len(title) < 3:
                return None

            # Цена
            price = self._extract_price(d)

            # URL
            raw_url = d.get("url") or d.get("link") or ""
            slug = d.get("slug") or d.get("id") or ""
            if raw_url.startswith("http"):
                url = raw_url
            elif raw_url:
                url = f"https://market.yandex.ru{raw_url}"
            else:
                url = f"https://market.yandex.ru/product--item/{slug}" if slug else "https://market.yandex.ru"

            # Картинка
            img = self._extract_image(d)

            # Характеристики
            chars: dict = {}
            brand = (
                d.get("brand")
                or (d.get("vendor") or {}).get("name")
                or (d.get("manufacturer") or {}).get("name")
                or ""
            )
            if brand:
                chars["Бренд"] = str(brand)

            specs = d.get("specs") or d.get("characteristics") or d.get("properties") or []
            if isinstance(specs, list):
                for spec in specs[:5]:
                    if isinstance(spec, dict):
                        k = spec.get("name") or spec.get("type") or ""
                        v = spec.get("value") or (spec.get("values") or [""])[0]
                        if k and v:
                            chars[str(k)] = str(v)

            rating = d.get("rating") or (d.get("ratings") or {}).get("value")
            reviews = d.get("reviewCount") or d.get("opinionsCount") or d.get("feedbackCount")

            return ProductItem(
                title=str(title)[:300],
                price=price,
                image_url=img,
                product_url=url,
                source=self.source_name,
                characteristics=chars,
                rating=float(rating) if rating else None,
                reviews_count=int(reviews) if reviews else None,
            )
        except Exception as exc:
            logger.debug(f"[YM] product parse error: {exc}")
            return None

    def _extract_price(self, d: dict) -> Optional[float]:
        for key in ("price", "prices", "offer", "minPrice"):
            v = d.get(key)
            if v is None:
                continue
            if isinstance(v, (int, float)):
                return float(v) if v > 0 else None
            if isinstance(v, dict):
                for sub in ("value", "min", "amount", "current", "price"):
                    sv = v.get(sub)
                    if sv is not None:
                        p = _clean_price(sv)
                        if p:
                            return p
            if isinstance(v, str):
                return _clean_price(v)
        return None

    def _extract_image(self, d: dict) -> Optional[str]:
        for key in ("picture", "image", "photo", "thumbnail"):
            v = d.get(key)
            if isinstance(v, str) and v:
                return v if v.startswith("http") else f"https:{v}"
        pics = d.get("pictures") or d.get("photos") or []
        if pics:
            first = pics[0]
            if isinstance(first, str):
                return first if first.startswith("http") else f"https:{first}"
            if isinstance(first, dict):
                u = first.get("url") or first.get("src") or ""
                return u if u.startswith("http") else f"https:{u}"
        return None

    def _parse_dom(self, html: str, limit: int) -> list[ProductItem]:
        """BS4 парсинг реального DOM (эффективен после Playwright-рендеринга)."""
        try:
            soup = BeautifulSoup(html, "lxml")
            results: list[ProductItem] = []

            candidates = (
                soup.find_all(attrs={"data-zone-name": "productSnippet"})
                or soup.find_all(attrs={"data-autotest-id": re.compile(r"product", re.I)})
                or soup.find_all("article")
            )

            for art in candidates[:limit * 2]:
                try:
                    title_el = (
                        art.find(attrs={"data-autotest-id": "product-name"})
                        or art.find("h3") or art.find("h2")
                        or art.find(class_=re.compile(r"title|name", re.I))
                    )
                    title = title_el.get_text(strip=True) if title_el else ""
                    if not title or len(title) < 3:
                        continue

                    price_el = (
                        art.find(attrs={"data-autotest-id": re.compile(r"price", re.I)})
                        or art.find(class_=re.compile(r"price", re.I))
                    )
                    price = _clean_price(
                        re.sub(r"[^\d]", "", price_el.get_text()) if price_el else None
                    )

                    link_el = art.find("a", href=True)
                    href = link_el["href"] if link_el else ""
                    url = href if href.startswith("http") else f"https://market.yandex.ru{href}"

                    img_el = art.find("img")
                    img = None
                    if img_el:
                        img = img_el.get("src") or img_el.get("data-src")
                        if img and img.startswith("//"):
                            img = f"https:{img}"

                    results.append(ProductItem(
                        title=title[:300],
                        price=price,
                        image_url=img,
                        product_url=url,
                        source=self.source_name,
                    ))
                    if len(results) >= limit:
                        break
                except Exception:
                    continue

            if results:
                logger.info(f"[YM DOM] {len(results)} items")
            return results

        except Exception as exc:
            logger.debug(f"[YM DOM] error: {exc}")
            return []
