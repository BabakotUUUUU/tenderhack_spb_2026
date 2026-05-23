"""
Seed URLs для краулера по категориям товаров.

Только сайты, проверенные на доступность (200 OK от httpx) и наличие
schema.org Product-разметки или microdata с ценами.

Исключены: Ozon, WB, Яндекс Маркет, Avito, AliExpress,
DNS, Citilink, MVideo, Eldorado, Megamarket, Sbermegamarket.
"""

from typing import TypedDict


class SeedSite(TypedDict):
    domain: str
    catalog_url: str        # страница каталога с продуктовыми ссылками
    search_pattern: str     # URL поиска с {query}
    category: str
    use_playwright: bool    # нужен ли Playwright для JS-рендеринга


# ── Шины ─────────────────────────────────────────────────────────────────────
# 4tochki.ru: доступен, JSON-LD Product с ценой на страницах моделей
TIRE_SEEDS: list[SeedSite] = [
    {
        "domain": "4tochki.ru",
        # www-URL напрямую — обходим redirect chain (308→301→200 vs 200)
        "catalog_url": "https://www.4tochki.ru/catalog/tyres/letnie-shini/",
        "search_pattern": "https://www.4tochki.ru/search/?query={query}",
        "category": "tires",
        "use_playwright": False,
    },
]

# ── Оргтехника / Офис ─────────────────────────────────────────────────────────
# foroffice.ru: доступен, JSON-LD Product + microdata с ценами
# Продуктовые страницы: /products/description/NNNNN.html
# Используем один seed (один catalog) чтобы не тянуть несколько 1.2MB страниц.
OFFICE_TECH_SEEDS: list[SeedSite] = [
    {
        "domain": "foroffice.ru",
        "catalog_url": "https://www.foroffice.ru/products/top_category/7.html",  # МФУ
        "search_pattern": "https://www.foroffice.ru/products/top_category/7.html",
        "category": "office_tech",
        "use_playwright": False,
    },
]

# ── Одежда / Обувь ────────────────────────────────────────────────────────────
# sportmaster.ru: 401 с proxy, нужен Playwright
# Используем поиск через 4tochki (нет, это шины) → GENERAL_SEEDS как fallback
CLOTHING_SEEDS: list[SeedSite] = [
    {
        "domain": "sportmaster.ru",
        "catalog_url": "https://www.sportmaster.ru/catalog/",
        "search_pattern": "https://www.sportmaster.ru/search/?searchText={query}",
        "category": "clothing",
        "use_playwright": True,
    },
    {
        "domain": "kari.com",
        "catalog_url": "https://www.kari.com/catalog/obuv/",
        "search_pattern": "https://www.kari.com/search/?q={query}",
        "category": "clothing",
        "use_playwright": True,
    },
]

# ── Общий ─────────────────────────────────────────────────────────────────────
# Смесь рабочих семян для запросов без конкретной категории.
GENERAL_SEEDS: list[SeedSite] = [
    {
        "domain": "4tochki.ru",
        "catalog_url": "https://www.4tochki.ru/catalog/tyres/letnie-shini/",
        "search_pattern": "https://www.4tochki.ru/search/?query={query}",
        "category": "general",
        "use_playwright": False,
    },
    {
        "domain": "foroffice.ru",
        "catalog_url": "https://www.foroffice.ru/products/top_category/7.html",
        "search_pattern": "https://www.foroffice.ru/products/top_category/7.html",
        "category": "general",
        "use_playwright": False,
    },
]

ALL_SEEDS: list[SeedSite] = TIRE_SEEDS + CLOTHING_SEEDS + OFFICE_TECH_SEEDS

# Домены, которые не нужно краулить (они уже охвачены основными парсерами)
EXCLUDED_DOMAINS: frozenset[str] = frozenset({
    "wildberries.ru", "wb.ru", "wbstatic.net",
    "ozon.ru", "api.ozon.ru",
    "market.yandex.ru", "yandex.ru",
    "avito.ru",
    "aliexpress.ru", "aliexpress.com",
    "dns-shop.ru",
    "citilink.ru",
    "mvideo.ru",
    "eldorado.ru",
    "sbermegamarket.ru", "sbermarket.ru", "megamarket.ru",
    "goods.ru",
    "lamoda.ru",
    "amazon.com", "amazon.co.uk",
    "youla.ru",
    "leroymerlin.ru",
    "ulmart.ru",
})


def get_seeds_for_category(category: str) -> list[SeedSite]:
    """Возвращает seed-сайты для заданной категории."""
    mapping = {
        "tires":       TIRE_SEEDS,
        "clothing":    CLOTHING_SEEDS,
        "office_tech": OFFICE_TECH_SEEDS,
        "general":     GENERAL_SEEDS,
    }
    return mapping.get(category, GENERAL_SEEDS)


def is_excluded(domain: str) -> bool:
    """Проверяет, нужно ли исключить домен из краулинга."""
    domain = domain.lower().lstrip("www.")
    return any(domain == exc or domain.endswith("." + exc) for exc in EXCLUDED_DOMAINS)
