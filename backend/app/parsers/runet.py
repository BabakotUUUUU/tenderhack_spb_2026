import asyncio
import re
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree

from app.parsers.browser import fetch_rendered_html
from app.parsers.common import ProductItem, SourceResult, default_geo
from app.parsers.extractors import extract_product_from_html, extract_product_links
from app.parsers.http_client import Fetcher, browser_headers

SITE_POOL = {
    "tires": ["4tochki.ru", "autoopt.ru", "shina-guide.ru", "tyres-auto.ru"],
    "office": ["foroffice.ru", "oldi.ru", "price.ru", "officemag.ru"],
    "clothes": ["1click.ru", "bonprix.ru", "kari.com", "sneakerhead.ru"],
}

SEARCH_PATTERNS = [
    "https://{host}/search/?q={q}",
    "https://{host}/search/?text={q}",
    "https://{host}/catalog/?q={q}",
    "https://{host}/?s={q}",
]

ADAPTERS = {
    "4tochki.ru": {
        "search": ["https://4tochki.ru/search/?q={q}", "https://4tochki.ru/catalog/tyres/?q={q}"],
        "allow": ["/catalog/tires/", "/catalog/tyres/", "/products/tyres/", "/tyres/"],
    },
    "foroffice.ru": {
        "search": ["https://www.foroffice.ru/search/?q={q}", "https://foroffice.ru/search/?q={q}"],
        "allow": ["/products/", "/catalog/"],
    },
    "oldi.ru": {
        "search": ["https://www.oldi.ru/search/?text={q}", "https://oldi.ru/search/?text={q}"],
        "allow": ["/catalog/element/", "/catalog/"],
    },
    "price.ru": {
        "search": ["https://price.ru/search/?query={q}", "https://price.ru/search/?q={q}"],
        "allow": ["/product/", "/offers/"],
    },
}


class RunetParser:
    source = "runet"

    async def search(self, query: str, region: str = "Москва", limit: int = 10, category: str = "tires") -> SourceResult:
        hosts = SITE_POOL.get(category, SITE_POOL["tires"])
        per_host = max(1, limit // max(1, len(hosts)) + 1)
        async with Fetcher() as fetcher:
            tasks = [self._search_host(fetcher, host, query, region, category, per_host) for host in hosts]
            chunks = await asyncio.gather(*tasks, return_exceptions=True)
        items: list[ProductItem] = []
        for chunk in chunks:
            if isinstance(chunk, list):
                items.extend(chunk)
        items = self._dedupe(items)[:limit]
        return SourceResult(self.source, "ok" if items else "empty", len(items), "", items)

    async def _search_host(self, fetcher: Fetcher, host: str, query: str, region: str, category: str, limit: int) -> list[ProductItem]:
        items: list[ProductItem] = []
        q = quote_plus(query)
        html = ""
        base_url = f"https://{host}/"
        adapter = ADAPTERS.get(host, {})
        links: list[str] = []
        patterns = adapter.get("search") or SEARCH_PATTERNS
        for pattern in patterns:
            url = pattern.format(host=host, q=q)
            resp = await fetcher.get_text(url, source=self.source, headers=browser_headers(referer=base_url, source=self.source), retries=0)
            if resp.blocked:
                rendered = await fetch_rendered_html(
                    url,
                    referer=base_url,
                    wait_selectors=['a[href*="/catalog/"]', 'a[href*="/product"]', ".product", ".item"],
                    scroll_steps=3,
                )
                html = rendered.html if rendered.status != "blocked" else ""
            else:
                html = resp.text
            links = self._filter_links(extract_product_links(html, url), host, adapter)
            if links:
                break
        if not links:
            links = await self._discover_links_from_sitemaps(fetcher, host, query, category, limit * 4)
        for link in links[: limit * 2]:
            detail = await fetcher.get_text(link, source=self.source, referer=base_url, retries=0)
            detail_html = detail.text
            if detail.blocked or not detail_html:
                rendered = await fetch_rendered_html(link, referer=base_url, wait_selectors=["h1", ".product", ".price"], scroll_steps=2)
                if rendered.status == "blocked" or not rendered.html:
                    continue
                detail_html = rendered.html
            item = extract_product_from_html(detail_html, link, self.source)
            if not item.title and not item.price:
                continue
            item.source = self.source
            item.sourceType = "runet"
            item.realSourceHost = urlparse(link).netloc
            item.category = category
            item.region = region
            item.geo = default_geo(region) | {k: v for k, v in item.geo.items() if v}
            self._enrich_geo_availability(item, detail_html, region)
            items.append(item)
            if len(items) >= limit:
                break
        return items

    async def _discover_links_from_sitemaps(self, fetcher: Fetcher, host: str, query: str, category: str, limit: int) -> list[str]:
        sitemap_urls = [f"https://{host}/sitemap.xml", f"https://{host}/sitemap_index.xml"]
        robots = await fetcher.get_text(f"https://{host}/robots.txt", source=self.source, retries=0)
        if robots.text:
            for match in re.findall(r"(?im)^sitemap:\s*(\S+)", robots.text):
                if match not in sitemap_urls:
                    sitemap_urls.append(match.strip())

        found: list[str] = []
        query_tokens = [t.lower() for t in re.findall(r"[a-zа-яё0-9]+", query, re.I) if len(t) > 2]
        category_hints = {
            "tires": ("shin", "tyre", "tire", "шины", "rezin", "catalog"),
            "office": ("printer", "mfu", "office", "орг", "canon", "hp", "catalog"),
            "clothes": ("odezh", "clothes", "kurt", "futbol", "sneaker", "catalog"),
        }.get(category, ("product", "catalog"))

        seen_sitemaps: set[str] = set()
        queue = sitemap_urls[:8]
        while queue and len(found) < limit:
            sitemap = queue.pop(0)
            if sitemap in seen_sitemaps:
                continue
            seen_sitemaps.add(sitemap)
            resp = await fetcher.get_text(sitemap, source=self.source, retries=0)
            if not resp.text or resp.blocked:
                continue
            urls = self._parse_sitemap_urls(resp.text)
            nested = [u for u in urls if "sitemap" in u.lower() and u not in seen_sitemaps]
            queue.extend(nested[:10])
            for url in urls:
                lower = url.lower()
                if urlparse(url).netloc and not urlparse(url).netloc.endswith(host):
                    continue
                if not any(hint in lower for hint in category_hints):
                    continue
                if query_tokens and not any(token in lower for token in query_tokens):
                    # Keep category matches too, but prefer query-like URLs.
                    if len(found) > limit // 2:
                        continue
                if url not in found:
                    found.append(url)
                if len(found) >= limit:
                    break
        return found[:limit]

    def _parse_sitemap_urls(self, xml_text: str) -> list[str]:
        urls: list[str] = []
        try:
            root = ElementTree.fromstring(xml_text.encode("utf-8"))
            for loc in root.iter():
                if loc.tag.lower().endswith("loc") and loc.text:
                    urls.append(loc.text.strip())
        except Exception:
            urls.extend(re.findall(r"<loc>\s*(.*?)\s*</loc>", xml_text, re.I | re.S))
        return urls[:1000]

    def _filter_links(self, links: list[str], host: str, adapter: dict) -> list[str]:
        allow = adapter.get("allow") or []
        out: list[str] = []
        for link in links:
            parsed = urlparse(link)
            if not parsed.netloc.endswith(host):
                continue
            if allow and not any(part in parsed.path for part in allow):
                continue
            if link not in out:
                out.append(link)
        return out

    def _enrich_geo_availability(self, item: ProductItem, html: str, region: str) -> None:
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or " ")).strip()
        lower = text.lower()
        if not item.availability:
            if any(marker in lower for marker in ("в наличии", "есть в наличии", "доступно", "на складе")):
                item.availability = "in_stock"
            elif any(marker in lower for marker in ("нет в наличии", "под заказ", "ожидается")):
                item.availability = "limited_or_out_of_stock"
        if not item.deliveryInfo:
            delivery = re.search(r"(доставк[а-яё\s]{0,30}(?:сегодня|завтра|от\s+\d+|[0-9]+\s*дн)[^.!?]{0,120})", text, re.I)
            if delivery:
                item.deliveryInfo = delivery.group(1).strip()
        address = re.search(r"((?:г\.?\s*)?(?:Москва|Санкт-Петербург|Новосибирск|Екатеринбург|Казань)[^.!?]{0,140}(?:ул\.|улица|пр-т|проспект|шоссе|д\.|дом)[^.!?]{0,160})", text, re.I)
        if address:
            item.geo["storeAddress"] = address.group(1).strip()
            item.geo["pickupAddress"] = item.geo.get("pickupAddress") or address.group(1).strip()
        if region and not item.geo.get("deliveryRegion"):
            item.geo["deliveryRegion"] = region
        city = re.search(r"(Москва|Санкт-Петербург|Новосибирск|Екатеринбург|Казань|Краснодар|Самара|Уфа)", text, re.I)
        if city:
            item.geo["detectedRegion"] = city.group(1)
            item.geo["city"] = city.group(1)

    def _dedupe(self, items: list[ProductItem]) -> list[ProductItem]:
        seen, out = set(), []
        for item in items:
            key = (item.url or f"{item.realSourceHost}:{item.title}").split("?")[0]
            if key and key not in seen:
                seen.add(key)
                out.append(item)
        return out
