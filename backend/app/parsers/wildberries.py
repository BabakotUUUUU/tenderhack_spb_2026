"""
Парсер Wildberries.
Использует официальный поисковый API WB (публичный, без ключей).
Endpoint: https://search.wb.ru/exactmatch/ru/common/v9/search
"""

import logging
from typing import Optional
from app.parsers.base import BaseParser, ProductItem, fetch_with_retry

logger = logging.getLogger(__name__)

# Маппинг регионов в dest-параметры WB (влияет на цены и наличие)
REGION_DEST: dict[str, str] = {
    "москва": "-1257786",
    "санкт-петербург": "-1275499",
    "спб": "-1275499",
    "новосибирск": "-364632",
    "екатеринбург": "-1198055",
    "казань": "-2133466",
    "нижний новгород": "-2096398",
    "краснодар": "-3520000",
    "default": "-1257786",
}


def _get_dest(region: str) -> str:
    return REGION_DEST.get(region.lower().strip(), REGION_DEST["default"])


class WildberriesParser(BaseParser):
    source_name = "Wildberries"
    domain = "search.wb.ru"

    async def search(self, query: str, region: str = "Москва", limit: int = 10) -> list[ProductItem]:
        dest = _get_dest(region)
        url = "https://search.wb.ru/exactmatch/ru/common/v9/search"
        params = {
            "query": query,
            "resultset": "catalog",
            "limit": min(limit, 20),
            "sort": "popular",
            "page": 1,
            "appType": 1,
            "curr": "rub",
            "dest": dest,
            "suppressSpellcheck": "false",
        }

        resp = await fetch_with_retry(self.client, url, self.domain, params=params)
        if not resp:
            return []

        try:
            data = resp.json()
        except Exception as e:
            logger.error(f"[WB] JSON parse error: {e}")
            return []

        products = data.get("data", {}).get("products", [])
        results: list[ProductItem] = []

        for p in products[:limit]:
            try:
                nm_id = p.get("id", "")
                name = p.get("name", "")
                brand = p.get("brand", "")
                title = f"{brand} {name}".strip() if brand else name

                # Цены в WB API в копейках
                price_data = p.get("sizes", [{}])[0].get("price", {})
                price_raw = price_data.get("product") or p.get("priceU", 0)
                price = round(price_raw / 100, 2) if price_raw else None

                # Изображение
                vol = nm_id // 100000
                part = nm_id // 1000
                image_url = f"https://basket-{_basket(vol):02d}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/c246x328/1.webp"

                product_url = f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx"

                # Характеристики
                chars = {}
                if brand:
                    chars["Бренд"] = brand
                rating = p.get("reviewRating")
                feedbacks = p.get("feedbacks")
                
                # Размеры/цвета
                colors = p.get("colors", [])
                if colors:
                    chars["Цвет"] = ", ".join(c.get("name", "") for c in colors[:3])

                results.append(ProductItem(
                    title=title,
                    price=price,
                    image_url=image_url,
                    product_url=product_url,
                    source=self.source_name,
                    characteristics=chars,
                    rating=rating,
                    reviews_count=feedbacks,
                ))
            except Exception as e:
                logger.warning(f"[WB] Error parsing product: {e}")
                continue

        logger.info(f"[WB] Found {len(results)} products for '{query}'")
        return results


def _basket(vol: int) -> int:
    """Определяет номер корзины WB по vol."""
    if vol <= 143:
        return 1
    elif vol <= 287:
        return 2
    elif vol <= 431:
        return 3
    elif vol <= 719:
        return 4
    elif vol <= 1007:
        return 5
    elif vol <= 1061:
        return 6
    elif vol <= 1115:
        return 7
    elif vol <= 1169:
        return 8
    elif vol <= 1313:
        return 9
    elif vol <= 1601:
        return 10
    elif vol <= 1655:
        return 11
    elif vol <= 1919:
        return 12
    elif vol <= 2045:
        return 13
    elif vol <= 2189:
        return 14
    elif vol <= 2405:
        return 15
    elif vol <= 2621:
        return 16
    elif vol <= 2837:
        return 17
    else:
        return 18
