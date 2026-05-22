"""
Парсер Ozon.

Стратегии (в порядке попытки):
  1. composer-API (v2) — внутренний JSON API Ozon
  2. entrypoint-API   — альтернативный внутренний endpoint
  3. HTML + embedded JSON — извлечение из <script type="application/json">
  4. HTML + BeautifulSoup — крайний fallback по DOM-структуре

Все варианты используют реалистичные заголовки браузера и паузы
для снижения вероятности блокировки.
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

_SEARCH_BASE = "https://www.ozon.ru/search/"

# Ключи, которые встречаются в JSON-дереве Ozon рядом с карточкой товара
_ITEM_MARKER_KEYS = {"mainImage", "tileImage", "skuId", "isAvailable"}


def _ozon_headers(referer: str = "https://www.ozon.ru/") -> dict:
    h = get_headers(referer)
    h.update({
        "x-o3-app-name": "ozonfront",
        "x-o3-app-version": "5.49.0",
        "x-o3-page-type": "search",
        "x-requested-with": "XMLHttpRequest",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    })
    return h


class OzonParser(BaseParser):
    source_name = "Ozon"
    domain = "www.ozon.ru"

    async def search(self, query: str, region: str = "Москва", limit: int = 10) -> list[ProductItem]:
        q_enc = quote(query)

        # Стратегия 1: composer-API v2
        results = await self._try_composer_api(q_enc, limit)
        if results:
            return results[:limit]

        # Стратегия 2: entrypoint-API
        results = await self._try_entrypoint_api(q_enc, limit)
        if results:
            return results[:limit]

        # Стратегия 3: HTML + embedded JSON
        results = await self._try_html_embedded(q_enc, limit)
        if results:
            return results[:limit]

        logger.warning(f"[Ozon] All strategies failed for '{query}'")
        return []

    # ------------------------------------------------------------------
    # Стратегия 1 — composer-API
    # ------------------------------------------------------------------
    async def _try_composer_api(self, q_enc: str, limit: int) -> list[ProductItem]:
        url = "https://api.ozon.ru/composer-api.bx/page/json/v2"
        params = {
            "url": f"/search/?text={q_enc}&from_global=true"
                   f"&layout_container=categorySearchMegapagination&layout_page_index=1",
        }
        try:
            await asyncio.sleep(random.uniform(1.5, 3.0))
            resp = await self.client.get(
                url, params=params, headers=_ozon_headers(), timeout=20.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = self._parse_widget_states(data, limit)
                if items:
                    logger.info(f"[Ozon] composer-API: {len(items)} items")
                    return items
        except Exception as e:
            logger.debug(f"[Ozon] composer-API failed: {e}")
        return []

    # ------------------------------------------------------------------
    # Стратегия 2 — entrypoint-API
    # ------------------------------------------------------------------
    async def _try_entrypoint_api(self, q_enc: str, limit: int) -> list[ProductItem]:
        url = "https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2"
        params = {"url": f"/search/?text={q_enc}&from_global=true"}
        try:
            await asyncio.sleep(random.uniform(1.5, 3.0))
            resp = await self.client.get(
                url, params=params,
                headers=_ozon_headers("https://www.ozon.ru/search/"),
                timeout=20.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = self._parse_widget_states(data, limit)
                if items:
                    logger.info(f"[Ozon] entrypoint-API: {len(items)} items")
                    return items
        except Exception as e:
            logger.debug(f"[Ozon] entrypoint-API failed: {e}")
        return []

    # ------------------------------------------------------------------
    # Стратегия 3 — HTML + embedded JSON
    # ------------------------------------------------------------------
    async def _try_html_embedded(self, q_enc: str, limit: int) -> list[ProductItem]:
        url = f"{_SEARCH_BASE}?text={q_enc}&from_global=true"
        try:
            await asyncio.sleep(random.uniform(2.5, 5.0))
            resp = await self.client.get(
                url,
                headers=get_headers("https://www.ozon.ru/"),
                timeout=25.0,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                logger.debug(f"[Ozon] HTML returned {resp.status_code}")
                return []

            html = resp.text

            # Ozon вставляет данные в несколько мест:
            # 1) <script type="application/json" id="state-XX">
            # 2) window.__NUXT__ = ...
            # 3) <script id="initial-state"> (старый формат)

            results: list[ProductItem] = []

            # Ищем все application/json скрипты
            soup = BeautifulSoup(html, "lxml")
            for script in soup.find_all("script", {"type": "application/json"}):
                try:
                    raw = script.string or ""
                    if not raw.strip():
                        continue
                    data = json.loads(raw)
                    items = list(self._walk_for_products(data, limit))
                    results.extend(items)
                    if len(results) >= limit:
                        break
                except Exception:
                    continue

            # Fallback: ищем widgetStates в inline-скриптах
            if not results:
                for m in re.finditer(r'"widgetStates"\s*:\s*(\{[^}]{50,}?\})', html):
                    try:
                        data = json.loads(m.group(1))
                        results.extend(self._parse_widget_states({"widgetStates": data}, limit))
                        if results:
                            break
                    except Exception:
                        continue

            if results:
                logger.info(f"[Ozon] HTML embedded: {len(results)} items")
            return results[:limit]

        except Exception as e:
            logger.debug(f"[Ozon] HTML fallback failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Парсинг widgetStates (формат compositor-API)
    # ------------------------------------------------------------------
    def _parse_widget_states(self, data: dict, limit: int) -> list[ProductItem]:
        results: list[ProductItem] = []
        widgets = data.get("widgetStates", {})
        for key, value in widgets.items():
            if not any(k in key for k in ("searchResultsV2", "tileGrid", "searchResults")):
                continue
            try:
                widget = json.loads(value) if isinstance(value, str) else value
                for item in widget.get("items", [])[:limit]:
                    p = self._parse_item(item)
                    if p:
                        results.append(p)
            except Exception:
                continue
            if len(results) >= limit:
                break
        return results

    # ------------------------------------------------------------------
    # Рекурсивный обход JSON — ищем объекты похожие на товар
    # ------------------------------------------------------------------
    def _walk_for_products(self, node, limit: int, _depth: int = 0):
        if _depth > 12:
            return
        if isinstance(node, dict):
            if self._looks_like_product(node):
                p = self._parse_item(node)
                if p:
                    yield p
                    return
            for v in node.values():
                yield from self._walk_for_products(v, limit, _depth + 1)
        elif isinstance(node, list):
            count = 0
            for elem in node:
                yield from self._walk_for_products(elem, limit, _depth + 1)
                count += 1
                if count >= limit * 3:
                    break

    def _looks_like_product(self, d: dict) -> bool:
        keys = set(d.keys())
        has_title = bool(keys & {"title", "name"})
        has_price = bool(keys & {"price", "finalPrice", "cardPrice"})
        has_id = bool(keys & {"skuId", "id", "itemId"})
        return has_title and (has_price or has_id)

    # ------------------------------------------------------------------
    # Разбор одного товара
    # ------------------------------------------------------------------
    def _parse_item(self, item: dict) -> Optional[ProductItem]:
        try:
            title = (
                item.get("title")
                or item.get("name")
                or item.get("displayName")
                or ""
            )
            if not title:
                return None

            # Ссылка
            link = (
                item.get("action", {}).get("link", "")
                or item.get("link", "")
                or item.get("url", "")
                or ""
            )
            item_id = item.get("skuId") or item.get("id") or item.get("itemId") or ""
            if link.startswith("/"):
                product_url = f"https://www.ozon.ru{link}"
            elif link.startswith("http"):
                product_url = link
            else:
                product_url = f"https://www.ozon.ru/product/{item_id}/" if item_id else "https://www.ozon.ru"

            # Цена
            price = self._extract_price(item)

            # Изображение
            image_url = self._extract_image(item)

            # Характеристики
            chars: dict = {}
            brand = item.get("brand") or item.get("brandName") or ""
            if brand:
                chars["Бренд"] = str(brand)
            rating = item.get("rating") or item.get("reviewRating")
            reviews = item.get("reviewsCount") or item.get("comments")

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
            logger.debug(f"[Ozon] item parse error: {e}")
            return None

    def _extract_price(self, item: dict) -> Optional[float]:
        for key in ("price", "finalPrice", "cardPrice", "originalPrice"):
            v = item.get(key)
            if v is None:
                continue
            if isinstance(v, dict):
                for subkey in ("price", "value", "amount"):
                    sv = v.get(subkey)
                    if sv is not None:
                        return self._parse_price_str(str(sv))
            else:
                p = self._parse_price_str(str(v))
                if p:
                    return p
        return None

    def _parse_price_str(self, s: str) -> Optional[float]:
        cleaned = re.sub(r"[^\d.]", "", s.replace(",", ".").replace(" ", ""))
        try:
            v = float(cleaned)
            return v if v > 0 else None
        except Exception:
            return None

    def _extract_image(self, item: dict) -> Optional[str]:
        for key in ("mainImage", "tileImage", "imageURL", "image"):
            v = item.get(key)
            if not v:
                continue
            if isinstance(v, str):
                return v if v.startswith("http") else f"https:{v}"
            if isinstance(v, list) and v:
                src = v[0] if isinstance(v[0], str) else v[0].get("url", "")
                return src if src.startswith("http") else f"https:{src}"
        images = item.get("images", [])
        if images:
            first = images[0] if isinstance(images[0], str) else images[0].get("url", "")
            return first if first.startswith("http") else f"https:{first}"
        return None
