# TenderHack SPB 2026 — Документация проекта

## Быстрый старт

```bash
docker compose build --no-cache && docker compose up
```

| Сервис | URL |
|--------|-----|
| Веб-интерфейс | http://localhost:3000 |
| API Swagger | http://localhost:8000/docs |
| Health | http://localhost:8000/api/health |

Сборка с нуля: ~7–10 мин (Playwright Chromium ~170 MB скачивается один раз).

---

## Технологический стек

### Backend

| Библиотека | Версия | Назначение |
|-----------|--------|-----------|
| Python | 3.12 | основной язык |
| FastAPI | 0.115.5 | веб-фреймворк, OpenAPI |
| uvicorn | 0.32.1 | ASGI-сервер (1 worker) |
| playwright | 1.47.0 | headless Chromium для парсинга JS-сайтов |
| httpx | 0.27.2 | async HTTP-клиент |
| BeautifulSoup4 | 4.12.3 | HTML-парсинг |
| lxml | 5.3.0 | быстрый C-парсер для BS4 |
| pydantic | 2.9.2 | валидация моделей |
| rapidfuzz | 3.10.1 | нечёткое сравнение строк (опечатки) |
| pymorphy3 | 2.0.2 | лемматизация русского языка |
| trafilatura | 1.12.2 | извлечение текста из веб-страниц |

### ML / Ранжирование

Собственный лексический ранкер без внешних зависимостей (`app/ml/ranker.py`):
- Токенное пересечение запроса и заголовка
- Бонус/штраф за полноту карточки (цена, фото, характеристики)
- Дедупликация по URL и нормализованному title+price

### 4-й источник (Рунет)

Собственный движок без внешних поисковых API:

| Компонент | Файл | Что делает |
|-----------|------|-----------|
| Crawler | `app/crawler/crawler.py` | обходит seed-сайты через Playwright (JS-рендеринг) |
| Extractor | `app/crawler/extractor.py` | JSON-LD → microdata → OG → heuristics |
| SQLite FTS5 | `app/search_index/db.py` | индекс + BM25-like поиск |
| Background | `app/crawler/background.py` | предварительная индексация при старте |

### Frontend

| Технология | Назначение |
|-----------|-----------|
| React 18 | UI |
| Vite 5 | сборщик |
| CSS Modules | стили |
| Nginx | статика + proxy /api → backend |

### Инфраструктура

| Компонент | Версия |
|-----------|--------|
| Docker Engine | ≥ 24 |
| Docker Compose | ≥ 2.20 (V2) |

---

## Архитектура

```
Пользователь (браузер)
       │
React SPA (Nginx :3000)
       │ /api/*
       ▼
Query Orchestrator (FastAPI search.py)
  NLP Layer:
    pymorphy3 → нормализация → rapidfuzz → опечатки → SYNONYM_MAP → синонимы
       │ asyncio.gather()
       ├─ WildberriesParser  → Playwright → wildberries.ru/catalog/0/search.aspx
       ├─ OzonParser         → Playwright → ozon.ru/search/
       ├─ YandexMarketParser → Playwright → market.yandex.ru/search
       └─ RunetParser        → SQLite FTS5 + Playwright Crawler → seed-сайты
       │
  Дедупликация + лексическое ранжирование
  Группировка по источникам
       │
JSON-ответ
```

---

## Парсеры

### Wildberries
- **URL:** `https://www.wildberries.ru/catalog/0/search.aspx?search={query}&dest={dest}`
- **Метод:** Playwright загружает страницу → DOM-парсинг карточек + embedded JSON в script-тегах
- **Регион:** Cookie `dest` + query-параметр `dest`
- **Данные:** title, price, image (по формуле basket/vol/part/nmId), ссылка

### Ozon
- **URL:** `https://www.ozon.ru/search/?text={query}`
- **Метод:** Playwright → DOM-парсинг (tileGrid, searchResultsV2) + JSON-LD + embedded JSON
- **Данные:** title, price, image, characteristics

### Яндекс Маркет
- **URL:** `https://market.yandex.ru/search?text={query}&lr={region_id}`
- **Метод:** Playwright + Cookie `_region_id` → `__NEXT_DATA__` JSON → DOM `article[data-zone-name]`
- **Регион:** параметр `lr` + Cookie

### Рунет (4-й нефиксированный источник)
1. **SQLite FTS5 индекс** — поиск по уже накопленным страницам
2. **Playwright Crawler** — загружает поисковую/каталожную страницу seed-сайта с JS-рендерингом
3. **httpx** — обходит найденные товарные ссылки (schema.org на статике)
4. **Extractor** — JSON-LD → microdata → OpenGraph → heuristics
5. **Индексирование** — результаты сохраняются для будущих запросов

Seed-сайты подбираются по категории запроса (шины / одежда / оргтехника).
Crawler следует по ссылкам → обнаруживает новые страницы и домены.

---

## NLP-пайплайн

```
Запрос "нотбук" →
  pymorphy3: лемматизация
  rapidfuzz: "нотбук" → "ноутбук" (порог 82%)
  Нормализация шин: "205/55r16" → "шины 205/55 R16"
  SYNONYM_MAP: "резина" → {шины, покрышки}
```

Всё работает локально, < 5 мс, нет внешних зависимостей.

---

## Rate Limiting

| Источник | Метод | Задержка |
|---------|-------|---------|
| Wildberries | Playwright (headless) | 1.5–2.5 сек |
| Ozon | Playwright | 1.5–2.5 сек |
| Яндекс Маркет | Playwright | 1.5–2.5 сек |
| Рунет seed pages | Playwright | 1.5–2.5 сек |
| Рунет product pages | httpx | 1.5–4 сек |

Дополнительно: ротация User-Agent, exponential backoff при 429, per-domain semaphore.

---

## Переменные окружения

Все прописаны в `docker-compose.yml` со значениями по умолчанию.
Дополнительный `.env` файл не нужен.

| Переменная | Дефолт | Описание |
|------------|--------|---------|
| `CACHE_ENABLED` | `true` | TTL-кэш ответов (20 мин) |
| `CACHE_TTL_SECONDS` | `1200` | TTL кэша в секундах |
| `PARSER_TIMEOUT_SECONDS` | `35` | таймаут одного парсера |
| `CRAWLER_DB_PATH` | `/data/runet_index.db` | путь SQLite индекса |
| `CRAWLER_MAX_PAGES_PER_DOMAIN` | `20` | лимит страниц с одного домена |

---

## BPMN

- Диаграмма (Mermaid): [docs/bpmn/price-search-process.mmd](docs/bpmn/price-search-process.mmd)
- Открыть на [mermaid.live](https://mermaid.live/) → Export PNG → в презентацию

---

## Ограничения

- Ozon и ЯМ защищены от ботов — на серверных IP первые запросы могут проходить, последующие могут возвращать пустой результат
- Рунет: первый запрос — live crawl (10–30 сек), повторный — из SQLite-индекса (< 1 сек)
- Playwright Chromium ~170 MB добавляется к Docker-образу при сборке
