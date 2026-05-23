"""
Экстрактор данных о товарах из HTML-страниц.

Порядок методов извлечения (от наиболее надёжного к эвристическому):
  1. JSON-LD schema.org/Product
  2. JSON-LD schema.org/Offer
  3. OpenGraph meta-теги
  4. Microdata (itemprop)
  5. HTML эвристики (CSS-селекторы, regex)
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Regex для извлечения цены из текста
_PRICE_RE = re.compile(
    r"(\d[\d\s]*[\d])"          # 1 234
    r"(?:[.,]\d{1,2})?"         # .99 или ,00
    r"\s*(?:₽|руб\.?|RUB|р\.)",
    re.IGNORECASE,
)

@dataclass
class ExtractedProduct:
    title: str
    price: Optional[float]
    old_price: Optional[float]
    image_url: Optional[str]
    url: str
    domain: str
    description: Optional[str]
    brand: Optional[str]
    category: Optional[str]
    characteristics: dict


def _clean_price(raw: str | float | int | None) -> Optional[float]:
    """Нормализует цену из разных форматов в float."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    s = str(raw).strip()
    # Убираем всё кроме цифр, запятых и точек
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        return None
    # Если есть и запятая и точка — определяем десятичный разделитель
    if "," in s and "." in s:
        # 12.999,00 → европейский формат
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        # 12999,00 или 12 999
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        v = float(s)
        return v if 10 <= v <= 10_000_000 else None
    except ValueError:
        return None


def _extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc.lstrip("www.")
    except Exception:
        return ""


def _try_json_ld(soup: BeautifulSoup, page_url: str) -> Optional[ExtractedProduct]:
    """Извлекает товар из JSON-LD разметки schema.org."""
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            raw = script.string or ""
            if not raw.strip():
                continue
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                t = item.get("@type", "")
                if t not in ("Product", "Offer", "AggregateOffer"):
                    # Recurse into @graph
                    graph = item.get("@graph", [])
                    for g in graph:
                        if isinstance(g, dict) and g.get("@type") == "Product":
                            item = g
                            t = "Product"
                            break
                    else:
                        continue

                title = item.get("name") or item.get("title") or ""
                if not title:
                    continue

                # Price from offers
                price = None
                old_price = None
                offers = item.get("offers", item if t == "Offer" else {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    price = _clean_price(offers.get("price") or offers.get("lowPrice"))
                    old_price = _clean_price(offers.get("highPrice"))

                # Image
                img = item.get("image")
                image_url = None
                if isinstance(img, str):
                    image_url = img
                elif isinstance(img, list) and img:
                    image_url = img[0] if isinstance(img[0], str) else img[0].get("url")
                elif isinstance(img, dict):
                    image_url = img.get("url")

                # Brand
                brand_data = item.get("brand")
                brand = None
                if isinstance(brand_data, dict):
                    brand = brand_data.get("name")
                elif isinstance(brand_data, str):
                    brand = brand_data

                # Characteristics from additionalProperty
                chars = {}
                for prop in item.get("additionalProperty", [])[:8]:
                    if isinstance(prop, dict):
                        k = prop.get("name", "")
                        v = prop.get("value", "")
                        if k and v:
                            chars[k] = str(v)
                if brand:
                    chars["Бренд"] = brand

                desc = item.get("description", "")
                if isinstance(desc, str):
                    desc = desc[:200]

                return ExtractedProduct(
                    title=title.strip(),
                    price=price,
                    old_price=old_price,
                    image_url=image_url,
                    url=page_url,
                    domain=_extract_domain(page_url),
                    description=desc,
                    brand=brand,
                    category=item.get("category"),
                    characteristics=chars,
                )
        except Exception as exc:
            logger.debug(f"[Extractor] JSON-LD parse error: {exc}")
            continue
    return None


def _try_opengraph(soup: BeautifulSoup, page_url: str) -> Optional[ExtractedProduct]:
    """Извлекает данные из OpenGraph meta-тегов."""
    def og(prop: str) -> Optional[str]:
        tag = soup.find("meta", {"property": f"og:{prop}"}) or \
              soup.find("meta", {"property": f"product:{prop}"})
        return tag.get("content") if tag else None

    title = og("title") or og("name")
    if not title:
        return None

    price_str = og("price:amount") or og("price")
    price = _clean_price(price_str)

    return ExtractedProduct(
        title=title.strip(),
        price=price,
        old_price=None,
        image_url=og("image"),
        url=page_url,
        domain=_extract_domain(page_url),
        description=og("description"),
        brand=og("brand"),
        category=og("type"),
        characteristics={},
    )


def _try_microdata(soup: BeautifulSoup, page_url: str) -> Optional[ExtractedProduct]:
    """Извлекает данные из HTML microdata (itemprop)."""
    # Только schema.org/Product — широкий fallback на любой itemscope/itemtype
    # ловил breadcrumb-элементы ("Каталог") вместо товара.
    product_el = soup.find(attrs={"itemtype": re.compile(r"schema\.org/Product", re.I)})
    if not product_el:
        return None

    def iprop(name: str) -> Optional[str]:
        el = product_el.find(attrs={"itemprop": name})
        if not el:
            return None
        return el.get("content") or el.get_text(strip=True)

    title = iprop("name")
    if not title:
        return None

    price_el = product_el.find(attrs={"itemprop": "price"})
    price = _clean_price(
        (price_el.get("content") or price_el.get_text(strip=True)) if price_el else None
    )

    img_el = product_el.find(attrs={"itemprop": "image"})
    image_url = None
    if img_el:
        image_url = img_el.get("src") or img_el.get("content")

    return ExtractedProduct(
        title=title.strip(),
        price=price,
        old_price=None,
        image_url=image_url,
        url=page_url,
        domain=_extract_domain(page_url),
        description=iprop("description"),
        brand=iprop("brand"),
        category=iprop("category"),
        characteristics={},
    )


def _try_heuristic(soup: BeautifulSoup, page_url: str) -> Optional[ExtractedProduct]:
    """HTML-эвристики как последний резерв."""
    # Title
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else ""
    if not title:
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
    if not title or len(title) < 4:
        return None

    # Price — ищем в тексте паттерн "1 234 ₽"
    price = None
    text = soup.get_text(" ", strip=True)
    m = _PRICE_RE.search(text)
    if m:
        price = _clean_price(m.group(1))

    # Image
    og_img = soup.find("meta", {"property": "og:image"})
    image_url = og_img.get("content") if og_img else None
    if not image_url:
        main_img = soup.find("img", class_=re.compile(r"product|main|hero", re.I))
        if main_img:
            image_url = main_img.get("src") or main_img.get("data-src")

    # Fix relative image URLs
    if image_url and not image_url.startswith("http"):
        image_url = urljoin(page_url, image_url)

    return ExtractedProduct(
        title=title[:200],
        price=price,
        old_price=None,
        image_url=image_url,
        url=page_url,
        domain=_extract_domain(page_url),
        description=None,
        brand=None,
        category=None,
        characteristics={},
    )


def _try_json_ld_regex(html: str, page_url: str) -> Optional[ExtractedProduct]:
    """
    Regex-based JSON-LD extraction from raw HTML (no truncation).
    Used before BeautifulSoup so JSON-LD near end of large pages is still found.
    Scans up to 1MB of HTML.
    """
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html[:1_000_000],
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            raw = m.group(1).strip()
            if not raw:
                continue
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                t = item.get("@type", "")
                if t not in ("Product", "Offer", "AggregateOffer"):
                    graph = item.get("@graph", [])
                    for g in graph:
                        if isinstance(g, dict) and g.get("@type") == "Product":
                            item = g
                            t = "Product"
                            break
                    else:
                        continue

                title = item.get("name") or item.get("title") or ""
                if not title:
                    continue

                price = None
                old_price = None
                offers = item.get("offers", item if t == "Offer" else {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    price = _clean_price(offers.get("price") or offers.get("lowPrice"))
                    old_price = _clean_price(offers.get("highPrice"))

                img = item.get("image")
                image_url = None
                if isinstance(img, str):
                    image_url = img
                elif isinstance(img, list) and img:
                    image_url = img[0] if isinstance(img[0], str) else (img[0].get("url") if isinstance(img[0], dict) else None)
                elif isinstance(img, dict):
                    image_url = img.get("url")

                brand_data = item.get("brand")
                brand = None
                if isinstance(brand_data, dict):
                    brand = brand_data.get("name")
                elif isinstance(brand_data, str):
                    brand = brand_data

                chars = {}
                for prop in item.get("additionalProperty", [])[:8]:
                    if isinstance(prop, dict):
                        k = prop.get("name", "")
                        v = prop.get("value", "")
                        if k and v:
                            chars[k] = str(v)
                if brand:
                    chars["Бренд"] = brand

                desc = item.get("description", "")
                if isinstance(desc, str):
                    desc = desc[:200]

                return ExtractedProduct(
                    title=str(title).strip(),
                    price=price,
                    old_price=old_price,
                    image_url=image_url,
                    url=page_url,
                    domain=_extract_domain(page_url),
                    description=desc,
                    brand=brand,
                    category=item.get("category"),
                    characteristics=chars,
                )
        except Exception as exc:
            logger.debug(f"[Extractor] JSON-LD regex parse error: {exc}")
            continue
    return None


def extract_product(html: str, page_url: str) -> Optional[ExtractedProduct]:
    """
    Основная функция извлечения товара из HTML.
    Пробует методы по убыванию надёжности.
    """
    if not html or len(html) < 200:
        return None

    # JSON-LD via regex первым — работает с полным HTML, без truncation
    # (JSON-LD часто находится ближе к концу страницы, после 500KB cutoff)
    ld_result = _try_json_ld_regex(html, page_url)
    if ld_result and ld_result.price:
        if ld_result.image_url and not ld_result.image_url.startswith("http"):
            ld_result.image_url = urljoin(page_url, ld_result.image_url)
        return ld_result

    # BeautifulSoup на усечённом HTML для остальных методов
    if len(html) > 500_000:
        html = html[:500_000]

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return None

    result = (
        _try_microdata(soup, page_url)
        or _try_opengraph(soup, page_url)
        or _try_heuristic(soup, page_url)
        or ld_result  # JSON-LD без цены как последний запасной вариант
    )

    if result and result.title:
        if result.image_url and not result.image_url.startswith("http"):
            result.image_url = urljoin(page_url, result.image_url)
        return result
    return None


def extract_product_links(html: str, base_url: str, max_links: int = 30) -> list[str]:
    """
    Извлекает ссылки на товарные страницы из страницы каталога.

    Паттерны рассчитаны на российские e-commerce сайты:
    - /product/, /item/, /catalog/ID/, /tovar/ — стандартные
    - /good/, /goods/ — nix.ru, regard.ru
    - /p/ID/, /n/ID/ — короткие форматы
    - /ID.html, /ID/ где ID это цифры ≥4 знаков — 1С-Bitrix, PrestaShop
    - /?id=ID, /?ID= — query-string ID
    """
    if not html:
        return []
    try:
        soup = BeautifulSoup(html[:400_000], "lxml")
    except Exception:
        return []

    base_netloc = urlparse(base_url).netloc

    # Паттерны путей типичных товарных страниц
    path_patterns = re.compile(
        r"/(?:"
        r"product|products|item|items|catalog/\w|tovar|tovary|goods?|"
        r"detail|details|card|cards|nomenklatura|pozitsiya|"
        r"p/\d|n/\d|good/\d|sku/\d|model/\d|"
        r"\d{4,}[/\-]"  # числовой ID в пути (артикул)
        r")[\w\-./]*",
        re.IGNORECASE,
    )
    # Паттерны query string с ID
    qs_patterns = re.compile(r"[?&](?:id|product_id|item_id|good_id)=\d+", re.IGNORECASE)

    links: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        # Только тот же домен
        if parsed.netloc != base_netloc:
            continue

        path = parsed.path
        qs = parsed.query

        if path_patterns.search(path) or qs_patterns.search(qs):
            clean = full.split("#")[0]
            links.add(clean)

        if len(links) >= max_links:
            break

    return list(links)[:max_links]
