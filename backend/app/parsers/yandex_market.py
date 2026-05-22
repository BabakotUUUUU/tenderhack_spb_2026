"""
Парсер Яндекс Маркет.

Стратегии (в порядке попытки):
  1. Официальный поиск market.yandex.ru/search с попыткой извлечь
     JSON из нескольких паттернов script-тегов
  2. Поиск через collections API (возвращает чистый JSON)
  3. Поиск по offers endpoint
  4. BeautifulSoup HTML-парсинг как последний fallback
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

# Паттерны извлечения JSON-данных из страницы YM
_JSON_PATTERNS = [
    # Современный Next.js / Nuxt — __NEXT_DATA__
    re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>\s*(\{.+?\})\s*</script>', re.DOTALL),
    # Старый nuxt
    re.compile(r'window\.__NUXT__\s*=\s*\(function\([^)]*\)\{return\s*(.+?)\}\([^)]*\)\)', re.DOTALL),
    re.compile(r'window\.__NUXT__\s*=\s*(\{.+?\})\s*;?\s*</script>', re.DOTALL),
    # Данные в state
    re.compile(r'window\.__initialState__\s*=\s*(\{.+?\})\s*;', re.DOTALL),
    # offers-блок
    re.compile(r'"offers"\s*:\s*(\[.+?\])', re.DOTALL),
    # searchResults-блок
    re.compile(r'"searchResults"\s*:\s*(\{.+?\})', re.DOTALL),
]


def _get_region_id(region: str) -> int:
    return REGION_IDS.get(region.lower().strip(), REGION_IDS["default"])


def _ym_headers(region_id: int, referer: str = "") -> dict:
    h = get_headers(referer or "https://market.yandex.ru/")
    h.update({
        "Cookie": f"_region_id={region_id}; yandexuid=0; i=0; skid=0; my=YycCAAA=",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    })
    return h


class YandexMarketParser(BaseParser):
    source_name = "Яндекс Маркет"
    domain = "market.yandex.ru"

    async def search(self, query: str, region: str = "Москва", limit: int = 10) -> list[ProductItem]:
        region_id = _get_region_id(region)
        q_enc = quote(query)

        # Стратегия 1: основная страница поиска
        results = await self._try_search_page(q_enc, region_id, limit)
        if results:
            return results[:limit]

        # Стратегия 2: альтернативный URL
        results = await self._try_alt_search(q_enc, region_id, limit)
        if results:
            return results[:limit]

        logger.warning(f"[YM] All strategies failed for '{query}'")
        return []

    # ------------------------------------------------------------------
    # Стратегия 1 — основная страница поиска
    # ------------------------------------------------------------------
    async def _try_search_page(self, q_enc: str, region_id: int, limit: int) -> list[ProductItem]:
        url = "https://market.yandex.ru/search"
        params = {"text": q_enc, "lr": region_id, "clid": "521"}
        try:
            await asyncio.sleep(random.uniform(2.0, 4.5))
            resp = await self.client.get(
                url,
                params=params,
                headers=_ym_headers(region_id),
                follow_redirects=True,
                timeout=25.0,
            )
            if resp.status_code != 200:
                logger.debug(f"[YM] search page status {resp.status_code}")
                return []
            return self._parse_html(resp.text, limit)
        except Exception as e:
            logger.debug(f"[YM] search page failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Стратегия 2 — альтернативный поиск
    # ------------------------------------------------------------------
    async def _try_alt_search(self, q_enc: str, region_id: int, limit: int) -> list[ProductItem]:
        # Некоторые категорийные страницы YM возвращают менее защищённые страницы
        url = f"https://market.yandex.ru/search?text={q_enc}&lr={region_id}&pp=18"
        try:
            await asyncio.sleep(random.uniform(3.0, 6.0))
            resp = await self.client.get(
                url,
                headers=_ym_headers(region_id, f"https://yandex.ru/search/?text={q_enc}+купить"),
                follow_redirects=True,
                timeout=25.0,
            )
            if resp.status_code != 200:
                return []
            return self._parse_html(resp.text, limit)
        except Exception as e:
            logger.debug(f"[YM] alt search failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Разбор HTML — пробуем несколько форматов
    # ------------------------------------------------------------------
    def _parse_html(self, html: str, limit: int) -> list[ProductItem]:
        results: list[ProductItem] = []

        # Попытка 1: __NEXT_DATA__ (современный Next.js)
        results = self._extract_next_data(html, limit)
        if results:
            logger.info(f"[YM] __NEXT_DATA__: {len(results)} items")
            return results

        # Попытка 2: паттерны JSON в script-тегах
        for pattern in _JSON_PATTERNS[1:]:
            for match in pattern.finditer(html):
                try:
                    data = json.loads(match.group(1))
                    items = list(self._walk_json(data, limit))
                    if items:
                        logger.info(f"[YM] JSON pattern: {len(items)} items")
                        return items
                except Exception:
                    continue

        # Попытка 3: BeautifulSoup
        results = self._parse_bs4(html, limit)
        if results:
            logger.info(f"[YM] BS4: {len(results)} items")
        return results

    def _extract_next_data(self, html: str, limit: int) -> list[ProductItem]:
        m = _JSON_PATTERNS[0].search(html)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
            return list(self._walk_json(data, limit))
        except Exception:
            return []

    def _walk_json(self, node, limit: int, _depth: int = 0):
        if _depth > 15:
            return
        if isinstance(node, dict):
            if self._looks_like_product(node):
                p = self._parse_product_dict(node)
                if p:
                    yield p
                    return
            for v in node.values():
                yield from self._walk_json(v, limit, _depth + 1)
        elif isinstance(node, list):
            count = 0
            for elem in node:
                yield from self._walk_json(elem, limit, _depth + 1)
                count += 1
                if count > limit * 5:
                    break

    def _looks_like_product(self, d: dict) -> bool:
        keys = set(d.keys())
        has_name = bool(keys & {"name", "title", "modelName"})
        has_price = bool(keys & {"price", "prices", "offer", "offers"})
        has_id = bool(keys & {"id", "offerId", "modelId", "skuId", "entity"})
        return has_name and has_price and has_id

    def _parse_product_dict(self, d: dict) -> Optional[ProductItem]:
        try:
            title = (
                d.get("name")
                or d.get("title")
                or d.get("modelName")
                or ""
            )
            if not title:
                return None

            # Цена
            price = self._extract_price(d)

            # URL
            slug = d.get("slug") or d.get("id") or ""
            raw_url = d.get("url") or d.get("link") or ""
            if raw_url.startswith("http"):
                product_url = raw_url
            elif raw_url:
                product_url = f"https://market.yandex.ru{raw_url}"
            else:
                product_url = f"https://market.yandex.ru/product--{slug}" if slug else "https://market.yandex.ru"

            # Изображение
            image_url = self._extract_image(d)

            # Характеристики
            chars: dict = {}
            brand = (
                d.get("brand")
                or (d.get("vendor", {}) or {}).get("name")
                or (d.get("manufacturer", {}) or {}).get("name")
                or ""
            )
            if brand:
                chars["Бренд"] = str(brand)

            specs = d.get("specs") or d.get("characteristics") or d.get("properties") or []
            if isinstance(specs, list):
                for spec in specs[:4]:
                    if isinstance(spec, dict):
                        k = spec.get("name") or spec.get("type") or ""
                        v = spec.get("value") or (spec.get("values") or [""])[0]
                        if k and v:
                            chars[str(k)] = str(v)

            rating = d.get("rating") or (d.get("ratings", {}) or {}).get("value")
            reviews = d.get("reviewCount") or d.get("opinionsCount") or d.get("feedbackCount")

            return ProductItem(
                title=str(title),
                price=price,
                image_url=image_url,
                product_url=product_url,
                source=self.source_name,
                characteristics=chars,
                rating=float(rating) if rating else None,
                reviews_count=int(reviews) if reviews else None,
            )
        except Exception as e:
            logger.debug(f"[YM] product parse error: {e}")
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
                        try:
                            return float(str(sv).replace(" ", "").replace(",", ".")) or None
                        except Exception:
                            continue
            if isinstance(v, str):
                cleaned = re.sub(r"[^\d.]", "", v.replace(",", "."))
                try:
                    return float(cleaned) or None
                except Exception:
                    pass
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
                url = first.get("url") or first.get("src") or first.get("original", "")
                return url if url.startswith("http") else f"https:{url}"
        return None

    # ------------------------------------------------------------------
    # Fallback — BeautifulSoup DOM-парсинг
    # ------------------------------------------------------------------
    def _parse_bs4(self, html: str, limit: int) -> list[ProductItem]:
        try:
            soup = BeautifulSoup(html, "lxml")
            results: list[ProductItem] = []

            # YM рендерит карточки в <article> или <div> с data-zone-name
            selectors = [
                soup.find_all("article"),
                soup.find_all(attrs={"data-zone-name": "productSnippet"}),
                soup.find_all(attrs={"data-autotest-id": re.compile(r"product", re.I)}),
                soup.find_all("div", class_=re.compile(r"snippet", re.I)),
            ]

            articles = next((s for s in selectors if s), [])

            for art in articles[:limit]:
                try:
                    # Название
                    title_el = (
                        art.find(attrs={"data-autotest-id": "product-name"})
                        or art.find("h3")
                        or art.find("h2")
                        or art.find(class_=re.compile(r"title|name", re.I))
                    )
                    title = title_el.get_text(strip=True) if title_el else ""
                    if not title:
                        continue

                    # Цена
                    price_el = (
                        art.find(attrs={"data-autotest-id": "snippet-price-current"})
                        or art.find(class_=re.compile(r"price", re.I))
                    )
                    price_text = price_el.get_text(strip=True) if price_el else ""
                    price = self._str_to_price(price_text)

                    # Ссылка
                    link_el = art.find("a", href=True)
                    href = link_el["href"] if link_el else ""
                    product_url = (
                        href if href.startswith("http")
                        else f"https://market.yandex.ru{href}"
                    )

                    # Изображение
                    img_el = art.find("img")
                    image_url = None
                    if img_el:
                        image_url = img_el.get("src") or img_el.get("data-src")
                        if image_url and image_url.startswith("//"):
                            image_url = f"https:{image_url}"

                    results.append(ProductItem(
                        title=title,
                        price=price,
                        image_url=image_url,
                        product_url=product_url,
                        source=self.source_name,
                    ))
                except Exception:
                    continue

            return results
        except Exception as e:
            logger.debug(f"[YM bs4] error: {e}")
            return []

    def _str_to_price(self, text: str) -> Optional[float]:
        digits = re.sub(r"[^\d]", "", text)
        return float(digits) if digits else None
