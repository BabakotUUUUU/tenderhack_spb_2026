"""
Seed URLs для краулера по категориям товаров.

Источник нефиксированный: краулер следует по ссылкам со страниц,
обнаруживает новые товарные страницы и домены — список постоянно растёт.

Исключены: Ozon, WB, Яндекс Маркет, Avito, AliExpress,
DNS, Citilink, MVideo, Eldorado, Megamarket, Sbermegamarket.
"""

from typing import TypedDict


class SeedSite(TypedDict):
    domain: str
    catalog_url: str        # запасная страница каталога (fallback)
    search_pattern: str     # URL поиска с {query}
    category: str


# ── Шины ─────────────────────────────────────────────────────────────────────
TIRE_SEEDS: list[SeedSite] = [
    {
        "domain": "4tochki.ru",
        "catalog_url": "https://4tochki.ru/catalog/tires/",
        "search_pattern": "https://4tochki.ru/catalog/tires/?width={query}",
        "category": "tires",
    },
    {
        "domain": "shina-guide.ru",
        "catalog_url": "https://shina-guide.ru/catalog/shiny/",
        "search_pattern": "https://shina-guide.ru/catalog/shiny/?search={query}",
        "category": "tires",
    },
    {
        "domain": "tyres-auto.ru",
        "catalog_url": "https://tyres-auto.ru/catalog/shiny/",
        "search_pattern": "https://tyres-auto.ru/catalog/shiny/?search={query}",
        "category": "tires",
    },
    {
        "domain": "autoopt.ru",
        "catalog_url": "https://autoopt.ru/catalog/tires/",
        "search_pattern": "https://autoopt.ru/search/?q={query}",
        "category": "tires",
    },
    {
        "domain": "tires-russia.ru",
        "catalog_url": "https://tires-russia.ru/catalog/",
        "search_pattern": "https://tires-russia.ru/search/?q={query}",
        "category": "tires",
    },
]

# ── Одежда ────────────────────────────────────────────────────────────────────
CLOTHING_SEEDS: list[SeedSite] = [
    {
        "domain": "sportmaster.ru",
        "catalog_url": "https://www.sportmaster.ru/catalog/",
        "search_pattern": "https://www.sportmaster.ru/search/?searchText={query}",
        "category": "clothing",
    },
    {
        "domain": "funday.ru",
        "catalog_url": "https://www.funday.ru/catalog/",
        "search_pattern": "https://www.funday.ru/search/?q={query}",
        "category": "clothing",
    },
    {
        "domain": "bonprix.ru",
        "catalog_url": "https://www.bonprix.ru/katalog/",
        "search_pattern": "https://www.bonprix.ru/suche/?keywords={query}",
        "category": "clothing",
    },
    {
        "domain": "kari.com",
        "catalog_url": "https://www.kari.com/catalog/obuv/",
        "search_pattern": "https://www.kari.com/search/?q={query}",
        "category": "clothing",
    },
    {
        "domain": "sneakerhead.ru",
        "catalog_url": "https://sneakerhead.ru/catalog/",
        "search_pattern": "https://sneakerhead.ru/search/?q={query}",
        "category": "clothing",
    },
    {
        "domain": "wildberries.ru",   # намеренно отсутствует — в excluded
        "catalog_url": "",
        "search_pattern": "",
        "category": "clothing",
    },
]
# Убираем пустые seeds (wildberries placeholder выше только для явности исключения)
CLOTHING_SEEDS = [s for s in CLOTHING_SEEDS if s["catalog_url"]]

# ── Оргтехника ────────────────────────────────────────────────────────────────
OFFICE_TECH_SEEDS: list[SeedSite] = [
    {
        "domain": "regard.ru",
        "catalog_url": "https://www.regard.ru/catalog/",
        "search_pattern": "https://www.regard.ru/search/?text={query}",
        "category": "office_tech",
    },
    {
        "domain": "pleer.ru",
        "catalog_url": "https://www.pleer.ru/",
        "search_pattern": "https://www.pleer.ru/search.html?q={query}",
        "category": "office_tech",
    },
    {
        "domain": "e2e4.ru",
        "catalog_url": "https://www.e2e4.ru/catalog/",
        "search_pattern": "https://www.e2e4.ru/search/?search={query}",
        "category": "office_tech",
    },
    {
        "domain": "nix.ru",
        "catalog_url": "https://www.nix.ru/",
        "search_pattern": "https://www.nix.ru/auto_category.html?search={query}",
        "category": "office_tech",
    },
    {
        "domain": "technopoint.ru",
        "catalog_url": "https://technopoint.ru/catalog/",
        "search_pattern": "https://technopoint.ru/search/?q={query}",
        "category": "office_tech",
    },
    {
        "domain": "computeruniverse.ru",
        "catalog_url": "https://www.computeruniverse.ru/",
        "search_pattern": "https://www.computeruniverse.ru/search/?q={query}",
        "category": "office_tech",
    },
]

# Общий список
GENERAL_SEEDS: list[SeedSite] = (
    TIRE_SEEDS[:2] + CLOTHING_SEEDS[:2] + OFFICE_TECH_SEEDS[:2]
)

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
