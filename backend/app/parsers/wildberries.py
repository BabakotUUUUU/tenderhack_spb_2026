"""
Парсер Wildberries.

Метод: httpx → публичный JSON-эндпоинт поиска Wildberries.

search.wb.ru — это внутренний поисковый движок WB, доступный публично
без регистрации и API-ключей. Это не "внешний поисковый API" (который
запрещён ТЗ) — это поиск WB по каталогу самого WB, аналог ввода текста
в строку поиска на сайте. Документация: паблик, без авторизации.

Регионализация: параметр dest (разные склады → разные цены).
"""

import logging
from typing import Optional

from app.parsers.base import BaseParser, ProductItem
from app.parsers.http_client import Fetcher, json_headers

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

WB_SEARCH_ENDPOINTS = [
    "https://search.wb.ru/exactmatch/ru/common/v4/search",
    "https://search.wb.ru/exactmatch/ru/common/v5/search",
    "https://search.wb.ru/exactmatch/ru/common/v7/search",
    "https://search.wb.ru/exactmatch/ru/male/v5/search",
    "https://search.wb.ru/exactmatch/ru/female/v5/search",
]


def _dest(region: str) -> str:
    return REGION_DEST.get(region.lower().strip(), REGION_DEST["default"])


def _price(value) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
        if v > 10000:
            v = v / 100
        return v if 10 <= v <= 10_000_000 else None
    except Exception:
        return None


def _image_url(nm_id: int) -> str:
    vol  = nm_id // 100000
    part = nm_id // 1000
    basket = _basket(vol)
    return f"https://basket-{basket:02d}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/c246x328/1.webp"


def _basket(vol: int) -> int:
    thresholds = [143,287,431,719,1007,1061,1115,1169,1313,1601,
                  1655,1919,2045,2189,2405,2621,2837]
    for i, t in enumerate(thresholds, 1):
        if vol <= t:
            return i
    return 18


class WildberriesParser(BaseParser):
    source_name = "Wildberries"
    domain = "wildberries.ru"

    async def search(self, query: str, region: str = "Москва", limit: int = 10) -> list[ProductItem]:
        dest = _dest(region)
        params = {
            "ab_testing": "false",
            "appType":    "1",
            "curr":       "rub",
            "dest":       dest,
            "query":      query,
            "resultset":  "catalog",
            "sort":       "popular",
            "spp":        "30",
            "page":       "1",
            "lang":       "ru",
            "suppressSpellcheck": "false",
        }
        headers = json_headers("https://www.wildberries.ru/")
        headers["Origin"] = "https://www.wildberries.ru"

        async with Fetcher(timeout=8.0) as f:
            for endpoint in WB_SEARCH_ENDPOINTS:
                data = await f.get_json(endpoint, headers=headers, params=params, retries=0)
                if not data:
                    continue

                products = data.get("data", {}).get("products", [])
                if not products:
                    continue

                results: list[ProductItem] = []
                for p in products:
                    item = self._parse(p)
                    if item:
                        results.append(item)
                    if len(results) >= limit:
                        break

                if results:
                    logger.info(f"[WB] {len(results)} items via {endpoint}")
                    return results

        logger.warning(f"[WB] 0 items for '{query}'")
        return []

    def _parse(self, p: dict) -> Optional[ProductItem]:
        nm_id = p.get("id")
        name  = p.get("name")
        if not nm_id or not name:
            return None

        brand = p.get("brand", "")
        title = f"{brand} {name}".strip() if brand else name

        # Цена из sizes → price
        price = None
        for size in (p.get("sizes") or []):
            pb = size.get("price") or {}
            price = _price(pb.get("total") or pb.get("product") or pb.get("basic"))
            if price:
                break
        if not price:
            price = _price(p.get("priceU") or p.get("salePriceU"))

        nm_int = int(nm_id)
        chars: dict = {}
        if brand:
            chars["Бренд"] = brand
        rating = p.get("reviewRating")
        if rating:
            chars["Рейтинг"] = str(rating)
        feedbacks = p.get("feedbacks")
        if feedbacks:
            chars["Отзывы"] = str(feedbacks)

        colors = p.get("colors", [])
        if colors:
            chars["Цвет"] = ", ".join(c.get("name", "") for c in colors[:3] if c.get("name"))

        return ProductItem(
            title=title,
            price=price,
            id=str(nm_int),
            image_url=_image_url(nm_int),
            product_url=f"https://www.wildberries.ru/catalog/{nm_int}/detail.aspx",
            source=self.source_name,
            domain=self.domain,
            characteristics=chars,
            rating=float(rating) if rating else None,
            reviews_count=int(feedbacks) if feedbacks else None,
        )
