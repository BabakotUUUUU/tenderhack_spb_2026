"""
Парсер Ozon.

Метод: Playwright (headless Chromium) + HTML/embedded JSON парсинг.

Ozon рендерит страницу поиска через React (клиентская сторона).
Playwright загружает страницу полностью, затем извлекаем данные из:
  1. JSON-LD schema.org/Product в script-тегах
  2. application/json script-тегов (embedded state)
  3. DOM-карточек после рендеринга (tileGrid, searchResults)
  4. Heuristic DOM — ссылки на product/ + извлечение цены/названия
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

_PRICE_RE = re.compile(r"(\d[\d\s]*\d)\s*₽")


def _parse_price(raw) -> Optional[float]:
    if raw is None:
        return None
    s = str(raw).replace(" ", "").replace("\xa0", "").replace(" ", "")
    s = re.sub(r"[^\d.]", "", s.replace(",", "."))
    try:
        v = float(s)
        return v if 10 <= v <= 10_000_000 else None
    except Exception:
        return None


class OzonParser(BaseParser):
    source_name = "Ozon"
    domain = "www.ozon.ru"

    async def search(self, query: str, region: str = "Москва", limit: int = 10) -> list[ProductItem]:
        results = await self._search_playwright(query, limit)
        if results:
            return results[:limit]
        logger.warning(f"[Ozon] Playwright returned 0 for '{query}'")
        return []

    # ------------------------------------------------------------------
    # Playwright — основной метод
    # ------------------------------------------------------------------
    async def _search_playwright(self, query: str, limit: int) -> list[ProductItem]:
        try:
            from app.parsers.browser import new_page
        except ImportError:
            return []

        page = None
        try:
            page = await new_page()
            url = f"https://www.ozon.ru/search/?text={quote(query)}&from_global=true"

            await page.goto(url, wait_until="domcontentloaded", timeout=25_000)

            # Ждём появления карточек товаров (или таймаута)
            try:
                await page.wait_for_selector(
                    "[data-widget='searchResultsV2'], "
                    "[data-widget='searchResultsSort'], "
                    "div[class*='tileGrid']",
                    timeout=10_000,
                )
            except Exception:
                pass  # попробуем парсить что есть

            await asyncio.sleep(random.uniform(1.5, 2.5))
            html = await page.content()

            return self._extract_from_html(html, limit)

        except Exception as exc:
            logger.warning(f"[Ozon Playwright] failed: {exc}")
            return []
        finally:
            if page:
                try:
                    await page.context.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Извлечение товаров из HTML (rendered Playwright или httpx)
    # ------------------------------------------------------------------
    def _extract_from_html(self, html: str, limit: int) -> list[ProductItem]:
        results: list[ProductItem] = []

        if len(html) > 1_000_000:
            html = html[:1_000_000]

        soup = BeautifulSoup(html, "lxml")

        # 1. JSON-LD schema.org/Product
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict) and item.get("@type") == "Product":
                        p = self._from_schema(item)
                        if p:
                            results.append(p)
            except Exception:
                continue
            if len(results) >= limit:
                return results

        # 2. Embedded JSON в script-тегах
        for script in soup.find_all("script", {"type": "application/json"}):
            try:
                data = json.loads(script.string or "")
                items = list(self._walk(data, limit - len(results)))
                results.extend(items)
            except Exception:
                continue
            if len(results) >= limit:
                return results

        # 3. DOM-карточки (Playwright рендерит их в реальный DOM)
        if len(results) < limit:
            dom_items = self._extract_dom_cards(soup, limit - len(results))
            results.extend(dom_items)

        logger.info(f"[Ozon] extracted {len(results)} items from HTML")
        return results[:limit]

    def _from_schema(self, item: dict) -> Optional[ProductItem]:
        title = item.get("name") or ""
        if not title:
            return None
        offers = item.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = _parse_price(offers.get("price") if isinstance(offers, dict) else None)
        imgs = item.get("image", [])
        img = imgs[0] if isinstance(imgs, list) and imgs else (imgs if isinstance(imgs, str) else None)
        brand = (item.get("brand") or {}).get("name") if isinstance(item.get("brand"), dict) else item.get("brand")
        chars = {"Бренд": brand} if brand else {}
        return ProductItem(
            title=title,
            price=price,
            image_url=img,
            product_url=item.get("url", "https://www.ozon.ru"),
            source=self.source_name,
            characteristics=chars,
        )

    def _extract_dom_cards(self, soup: BeautifulSoup, limit: int) -> list[ProductItem]:
        """Извлекает карточки из реального DOM (только после Playwright-рендеринга)."""
        results = []
        # Ozon рендерит карточки с data-index или внутри tileGrid-контейнеров
        selectors = [
            {"data-widget": "tileGrid"},
            {"data-widget": "searchResultsV2"},
        ]
        container = None
        for sel in selectors:
            container = soup.find(attrs=sel)
            if container:
                break

        if not container:
            container = soup

        links_seen: set[str] = set()
        for a_tag in container.find_all("a", href=True):
            href = a_tag.get("href", "")
            if "/product/" not in href:
                continue
            url = f"https://www.ozon.ru{href}" if href.startswith("/") else href
            if url in links_seen:
                continue
            links_seen.add(url)

            # Пытаемся извлечь цену и название из контекста ссылки
            card = a_tag.find_parent("div", recursive=True) or a_tag
            title_el = (
                card.find("span", {"class": re.compile(r"title|name|product", re.I)})
                or card.find("a")
            )
            title = title_el.get_text(strip=True) if title_el else a_tag.get_text(strip=True)
            if not title or len(title) < 4:
                continue

            price_text = ""
            for el in card.find_all(string=_PRICE_RE):
                m = _PRICE_RE.search(el)
                if m:
                    price_text = m.group(1)
                    break
            price = _parse_price(price_text)

            img = card.find("img")
            img_src = img.get("src") or img.get("data-src") if img else None

            results.append(ProductItem(
                title=title[:300],
                price=price,
                image_url=img_src,
                product_url=url,
                source=self.source_name,
            ))
            if len(results) >= limit:
                break

        return results

    def _parse_api_response(self, data: dict, limit: int) -> list[ProductItem]:
        results = []
        for key, value in data.get("widgetStates", {}).items():
            if not any(k in key for k in ("searchResultsV2", "tileGrid", "searchResults")):
                continue
            try:
                widget = json.loads(value) if isinstance(value, str) else value
                for item in widget.get("items", [])[:limit]:
                    p = self._item_from_api(item)
                    if p:
                        results.append(p)
            except Exception:
                continue
            if len(results) >= limit:
                break
        return results

    def _item_from_api(self, item: dict) -> Optional[ProductItem]:
        title = item.get("title") or item.get("name") or ""
        if not title:
            return None
        link = item.get("action", {}).get("link", "") or item.get("link", "") or item.get("url", "")
        url = f"https://www.ozon.ru{link}" if link.startswith("/") else (link or "https://www.ozon.ru")
        price = _parse_price(
            item.get("price", {}).get("price") if isinstance(item.get("price"), dict) else item.get("price")
        )
        imgs = item.get("images") or item.get("mainImage") or []
        img = imgs[0] if isinstance(imgs, list) and imgs else (imgs if isinstance(imgs, str) else None)
        brand = item.get("brand") or item.get("brandName") or ""
        chars = {"Бренд": brand} if brand else {}
        return ProductItem(title=title, price=price, image_url=img, product_url=url,
                           source=self.source_name, characteristics=chars)

    def _walk(self, node, limit: int, depth: int = 0):
        if depth > 10 or limit <= 0:
            return
        if isinstance(node, dict):
            if self._looks_like_product(node):
                p = self._item_from_api(node)
                if p:
                    yield p
                    return
            for v in node.values():
                yield from self._walk(v, limit, depth + 1)
        elif isinstance(node, list):
            for elem in node[:limit * 3]:
                yield from self._walk(elem, limit, depth + 1)

    def _looks_like_product(self, d: dict) -> bool:
        keys = set(d.keys())
        return bool(keys & {"title", "name"}) and bool(keys & {"price", "finalPrice"}) and bool(keys & {"id", "skuId"})
