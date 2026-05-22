"""
Парсер Wildberries.

Метод: Playwright (headless Chromium) + HTML/embedded JSON парсинг
страницы поиска wildberries.ru.

Wildberries рендерит результаты поиска через JavaScript (React).
Playwright загружает страницу целиком, затем извлекаем данные из:
  1. Embedded JSON в <script> тегах (window.__WB_STATE__, nmId-блоки)
  2. DOM-карточек article.product-card с data-атрибутами
  3. Стандартных CSS-классов product-card__name, price__lower-price

Регионализация: Cookie dest + query-параметр dest.
"""

import asyncio
import logging
import random
import re
from typing import Optional
from urllib.parse import quote

from bs4 import BeautifulSoup

from app.parsers.base import BaseParser, ProductItem

logger = logging.getLogger(__name__)

REGION_DEST: dict[str, str] = {
    "москва":          "-1257786",
    "санкт-петербург": "-1275499",
    "спб":             "-1275499",
    "новосибирск":     "-364632",
    "екатеринбург":    "-1198055",
    "казань":          "-2133466",
    "нижний новгород": "-2096398",
    "краснодар":       "-3520000",
    "ростов-на-дону":  "-3827144",
    "уфа":             "-587615",
    "самара":          "-2578754",
    "омск":            "-3634030",
    "default":         "-1257786",
}


def _get_dest(region: str) -> str:
    return REGION_DEST.get(region.lower().strip(), REGION_DEST["default"])


def _clean_price(raw) -> Optional[float]:
    if raw is None:
        return None
    s = re.sub(r"[^\d]", "", str(raw).replace(" ", "").replace("\xa0", ""))
    try:
        v = float(s)
        return v if 10 <= v <= 10_000_000 else None
    except Exception:
        return None


class WildberriesParser(BaseParser):
    source_name = "Wildberries"
    domain = "www.wildberries.ru"

    async def search(self, query: str, region: str = "Москва", limit: int = 10) -> list[ProductItem]:
        dest = _get_dest(region)
        results = await self._search_playwright(query, dest, limit)
        if results:
            return results[:limit]
        logger.warning(f"[WB] Playwright returned 0 results for '{query}'")
        return []

    async def _search_playwright(self, query: str, dest: str, limit: int) -> list[ProductItem]:
        try:
            from app.parsers.browser import new_page
        except ImportError:
            return []

        page = None
        try:
            page = await new_page()
            await page.context.add_cookies([
                {"name": "dest",   "value": dest,   "domain": ".wildberries.ru", "path": "/"},
                {"name": "region", "value": "80,64,83,4,38,33,70,82,86,75,30",
                 "domain": ".wildberries.ru", "path": "/"},
            ])

            url = (
                f"https://www.wildberries.ru/catalog/0/search.aspx"
                f"?search={quote(query)}&dest={dest}"
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=25_000)

            try:
                await page.wait_for_selector(
                    "article.product-card, [class*='product-card'], [data-nm-id]",
                    timeout=12_000,
                )
            except Exception:
                pass

            await asyncio.sleep(random.uniform(1.5, 2.5))
            html = await page.content()
            return self._parse_html(html, limit)

        except Exception as exc:
            logger.warning(f"[WB Playwright] failed: {exc}")
            return []
        finally:
            if page:
                try:
                    await page.context.close()
                except Exception:
                    pass

    def _parse_html(self, html: str, limit: int) -> list[ProductItem]:
        if len(html) > 2_000_000:
            html = html[:2_000_000]
        soup = BeautifulSoup(html, "lxml")

        # Попытка 1: embedded JSON с nmId
        results = self._extract_from_scripts(soup, limit)
        if results:
            logger.info(f"[WB] script JSON: {len(results)} items")
            return results

        # Попытка 2: DOM карточки (после Playwright-рендеринга)
        results = self._extract_from_dom(soup, limit)
        if results:
            logger.info(f"[WB] DOM cards: {len(results)} items")
        return results

    def _extract_from_scripts(self, soup: BeautifulSoup, limit: int) -> list[ProductItem]:
        results: list[ProductItem] = []
        for script in soup.find_all("script"):
            src = script.string or ""
            if len(src) < 100:
                continue
            for m in re.finditer(r'"nm(?:Id|ID)"\s*:\s*(\d+)', src):
                try:
                    nm_id = m.group(1)
                    pos = m.start()
                    chunk = src[max(0, pos - 600):pos + 600]
                    item = self._parse_json_chunk(chunk, nm_id)
                    if item:
                        results.append(item)
                        if len(results) >= limit:
                            return results
                except Exception:
                    continue
        return results

    def _parse_json_chunk(self, chunk: str, nm_id: str) -> Optional[ProductItem]:
        name_m = re.search(r'"(?:name|title)"\s*:\s*"([^"]{4,200})"', chunk)
        if not name_m:
            return None
        title = name_m.group(1)

        brand_m = re.search(r'"brand(?:Name)?"\s*:\s*"([^"]+)"', chunk)
        brand = brand_m.group(1) if brand_m else ""
        full_title = f"{brand} {title}".strip() if brand else title

        price = None
        for key in (r'"salePriceU"', r'"priceU"', r'"price"'):
            pm = re.search(key + r'\s*:\s*(\d+)', chunk)
            if pm:
                val = int(pm.group(1))
                price = val / 100 if val > 10000 else float(val)
                if 10 <= price <= 10_000_000:
                    break
                price = None

        nm_int = int(nm_id)
        chars: dict = {"Бренд": brand} if brand else {}
        return ProductItem(
            title=full_title,
            price=price,
            image_url=self._build_image_url(nm_int),
            product_url=f"https://www.wildberries.ru/catalog/{nm_int}/detail.aspx",
            source=self.source_name,
            characteristics=chars,
        )

    def _extract_from_dom(self, soup: BeautifulSoup, limit: int) -> list[ProductItem]:
        results: list[ProductItem] = []
        cards = (
            soup.find_all("article", class_=re.compile(r"product-card", re.I))
            or soup.find_all(attrs={"data-nm-id": True})
        )
        for card in cards[:limit * 2]:
            try:
                nm_id = card.get("data-nm-id") or card.get("data-id")

                title_el = (
                    card.find("span", class_=re.compile(r"product-card__name|goods-name", re.I))
                    or card.find(attrs={"data-name": True})
                )
                title = (
                    title_el.get("data-name") or title_el.get_text(strip=True)
                ) if title_el else card.get("data-name", "")
                if not title or len(title) < 3:
                    continue

                brand_el = card.find(class_=re.compile(r"brand", re.I))
                brand = brand_el.get_text(strip=True) if brand_el else card.get("data-brand", "")

                price_el = (
                    card.find("ins", class_=re.compile(r"price", re.I))
                    or card.find(class_=re.compile(r"price__lower", re.I))
                )
                price = _clean_price(
                    re.sub(r"[^\d]", "", price_el.get_text()) if price_el else None
                )

                link_el = card.find("a", href=True)
                href = link_el["href"] if link_el else ""
                if href.startswith("/"):
                    product_url = f"https://www.wildberries.ru{href}"
                elif href.startswith("http"):
                    product_url = href
                elif nm_id:
                    product_url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"
                else:
                    continue

                id_m = re.search(r"/catalog/(\d+)/", product_url)
                nm_int = int(id_m.group(1)) if id_m else None

                img_el = card.find("img")
                image_url = None
                if img_el:
                    image_url = img_el.get("src") or img_el.get("data-src")
                    if image_url and image_url.startswith("//"):
                        image_url = f"https:{image_url}"
                if not image_url and nm_int:
                    image_url = self._build_image_url(nm_int)

                results.append(ProductItem(
                    title=title,
                    price=price,
                    image_url=image_url,
                    product_url=product_url,
                    source=self.source_name,
                    characteristics={"Бренд": brand} if brand else {},
                ))
                if len(results) >= limit:
                    break
            except Exception:
                continue
        return results

    def _build_image_url(self, nm_id: int) -> Optional[str]:
        try:
            vol  = nm_id // 100000
            part = nm_id // 1000
            b = self._basket(vol)
            return (
                f"https://basket-{b:02d}.wbbasket.ru"
                f"/vol{vol}/part{part}/{nm_id}/images/c246x328/1.webp"
            )
        except Exception:
            return None

    @staticmethod
    def _basket(vol: int) -> int:
        for i, t in enumerate(
            [143,287,431,719,1007,1061,1115,1169,1313,1601,1655,1919,2045,2189,2405,2621,2837], 1
        ):
            if vol <= t:
                return i
        return 18
