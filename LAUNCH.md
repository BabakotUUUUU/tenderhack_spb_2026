# TenderHack SPB 2026 — Полная документация проекта

> Интеллектуальный сервис поиска цен на товары в открытых источниках Рунета.
> Хакатон «Tender Hack — Санкт-Петербург», 2026.
> Организаторы: Портал Поставщиков · Департамент конкурентной политики · ДИТ Москвы.

---

## Оглавление

1. [Полный технологический стек](#1-полный-технологический-стек)
2. [Архитектура системы](#2-архитектура-системы)
3. [Быстрый старт](#3-быстрый-старт)
4. [Локальная разработка без Docker](#4-локальная-разработка-без-docker)
5. [Парсеры — как и откуда берутся данные](#5-парсеры)
6. [NLP-пайплайн — обработка запросов](#6-nlp-пайплайн)
7. [ML-ранжирование — нейросеть](#7-ml-ранжирование)
8. [Rate limiting — обход блокировок](#8-rate-limiting)
9. [API-контракты](#9-api-контракты)
10. [Переменные окружения](#10-переменные-окружения)
11. [BPMN-схема процесса](#11-bpmn-схема)
12. [Ограничения и компромиссы](#12-ограничения)

---

## 1. Полный технологический стек

### Backend

| Библиотека | Версия | Назначение |
|-----------|--------|-----------|
| **Python** | 3.12 | основной язык серверной части |
| **FastAPI** | 0.115.5 | веб-фреймворк, OpenAPI-документация, валидация через Pydantic |
| **uvicorn** | 0.32.1 | ASGI-сервер, запускает FastAPI; `[standard]` добавляет uvloop и httptools для скорости |
| **httpx** | 0.27.2 | асинхронный HTTP-клиент; используется во всех парсерах вместо requests, поддерживает async/await |
| **BeautifulSoup4** | 4.12.3 | парсинг HTML-страниц маркетплейсов и сайтов Рунета |
| **lxml** | 5.3.0 | быстрый C-парсер для BeautifulSoup (`html.parser` медленнее в 3–5×); нужен gcc при сборке |
| **pydantic** | 2.9.2 | валидация и сериализация моделей ответа API; v2 написана на Rust — значительно быстрее v1 |
| **rapidfuzz** | 3.10.1 | нечёткое сравнение строк (расстояние Левенштейна) для исправления опечаток |
| **pymorphy3** | 2.0.2 | морфологический анализатор русского языка; лемматизация: «ноутбуки» → «ноутбук» |
| **trafilatura** | 1.12.2 | извлечение основного текста из веб-страниц (удаляет навигацию, рекламу, footer) |
| **fastembed** | 0.4.2 | ONNX-based инференс трансформерных моделей без PyTorch; используется для ML-ранжирования |
| **numpy** | ≥ 1.26.0 | косинусное сходство векторов в ML-ранкере |
| **python-multipart** | 0.0.12 | обработка multipart/form-data (требование FastAPI) |

### ML-модель

| Модель | Формат | Размер | Язык | Задача |
|--------|--------|--------|------|--------|
| `paraphrase-multilingual-MiniLM-L12-v2` | ONNX | ~120 MB | 50+ языков вкл. русский | семантические эмбеддинги для ре-ранкинга |

Модель загружается **при сборке Docker-образа** (`docker compose build`) и кешируется в `/opt/fastembed_cache`. Инференс на CPU: ~30–80 мс на батч из 20 товаров.

### Frontend

| Технология | Версия | Назначение |
|-----------|--------|-----------|
| **React** | 18.3.1 | UI-фреймворк, компонентная архитектура |
| **React DOM** | 18.3.1 | рендеринг React в браузере |
| **Vite** | 5.4.9 | сборщик, dev-сервер с HMR, компилирует JSX и бандлит CSS Modules |
| **@vitejs/plugin-react** | 4.3.2 | Babel-трансформация JSX для Vite |
| **CSS Modules** | встроен в Vite | изолированные стили на уровне компонента, нет конфликтов классов |
| **axios** | 1.7.7 | HTTP-клиент для запросов к backend API |
| **Nginx** | alpine | production-сервер статики + reverse proxy `/api → backend:8000` |

### Инфраструктура

| Технология | Версия | Назначение |
|-----------|--------|-----------|
| **Docker Engine** | ≥ 24 | изоляция сервисов в контейнерах |
| **Docker Compose** | ≥ 2.20 (V2) | оркестрация трёх контейнеров, healthchecks, сеть |
| **SearXNG** | latest (AGPL-3.0) | self-hosted агрегатор поиска — 4-й нефиксированный источник |

### Форматы и конфиги

| Файл | Формат | Зачем |
|------|--------|-------|
| `docker-compose.yml` | YAML | описание сервисов, сетей, healthchecks |
| `searxng/settings.yml` | YAML | настройки SearXNG: движки, форматы, rate limits |
| `searxng/limiter.toml` | TOML | bot-detection конфиг SearXNG; Docker-сеть в whitelist |
| `frontend/nginx.conf` | Nginx | proxy `/api/` → backend, SPA fallback `try_files` |
| `frontend/vite.config.js` | JS | proxy `/api` → localhost:8000 для dev-режима |

---

## 2. Архитектура системы

```
Пользователь (браузер)
        │ HTTP :3000
        ▼
┌───────────────────────────────────────────────────────────────┐
│                     Docker Network (bridge)                    │
│                                                               │
│  ┌─────────────────┐       ┌──────────────────────────────┐   │
│  │    Frontend     │ /api/ │          Backend             │   │
│  │  React + Nginx  │──────▶│       FastAPI + uvicorn      │   │
│  │  :80 → :3000    │       │  Python 3.12  │  :8000       │   │
│  └─────────────────┘       └──────┬──────┬─┴──────────────┘   │
│                                   │      │                     │
│                          ┌────────┘      └────────┐           │
│                          ▼                         ▼           │
│                 ┌─────────────────┐   ┌──────────────────┐    │
│                 │    NLP Layer    │   │    ML Layer      │    │
│                 │  pymorphy3      │   │  fastembed       │    │
│                 │  rapidfuzz      │   │  MiniLM-L12-v2   │    │
│                 │  synonym map    │   │  ONNX Runtime    │    │
│                 └────────┬────────┘   └────────┬─────────┘    │
│                          │ primary_query        │ ranked items │
│                          ▼                      │              │
│             ┌────────────────────────┐          │              │
│             │   asyncio.gather()     │──────────┘              │
│             │  параллельные парсеры  │                         │
│             └──┬──────┬──────┬───┬──┘                         │
│                │      │      │   │                             │
│  ┌─────────────┐ ┌────┐ ┌───┐ ┌─┴──────────────────────────┐ │
│  │ Wildberries │ │Ozon│ │ YM│ │        SearXNG :8080        │ │
│  │  search API │ │    │ │   │ │   DuckDuckGo + Bing + Google│ │
│  └─────────────┘ └────┘ └───┘ └─────────────────────────────┘ │
└───────────────────────────────────────────────────────────────┘
                                   │ SearXNG results
                                   ▼
                        Рунет (произвольные сайты)
                        trafilatura + schema.org
```

### Порядок старта контейнеров

```
searxng (healthcheck: /healthz) ──ready──▶ backend (healthcheck: /api/health) ──ready──▶ frontend
```

Без healthchecks backend стартовал до готовности SearXNG и сыпал ошибками подключения.

---

## 3. Быстрый старт

### Требования

- **Docker Desktop** ≥ 24 (Windows/Mac) или **Docker Engine** ≥ 24 (Linux)
- Интернет для скачивания образов и ML-модели при первой сборке

### Команды

```bash
# Клонировать репозиторий
git clone https://github.com/Oleg4311/tenderhack_spb_2026.git
cd tenderhack_spb_2026

# Linux/Mac — дать права SearXNG на запись конфига
chmod 777 searxng/

# Собрать образы и запустить
docker compose up --build
```

Первый запуск: **7–12 минут** (скачивание образов + ML-модель ~120 MB + npm build).
Повторный запуск `docker compose up`: **15–30 секунд**.

### Адреса

| Сервис | URL | Описание |
|--------|-----|----------|
| **Веб-интерфейс** | http://localhost:3000 | основное приложение |
| **Swagger UI** | http://localhost:8000/docs | интерактивная документация API |
| **ReDoc** | http://localhost:8000/redoc | альтернативная документация |
| **SearXNG** | http://localhost:8080 | поисковый агрегатор (4-й источник) |

### Makefile — удобные команды

```bash
make fresh         # пересобрать и запустить с нуля
make up            # запустить (без пересборки)
make down          # остановить все контейнеры
make logs          # логи всех сервисов в реальном времени
make logs-backend  # только backend логи
make status        # статус контейнеров
make health        # curl /api/health
make clean         # удалить контейнеры, volumes, networks
```

---

## 4. Локальная разработка без Docker

### Backend (Python)

```bash
# Виртуальное окружение
python -m venv .venv
source .venv/bin/activate          # Linux/Mac
.venv\Scripts\activate             # Windows

# Зависимости
pip install -r backend/requirements.txt

# SearXNG всё равно нужен для 4-го источника
docker compose up searxng -d

# Запуск с hot-reload
cd backend
SEARXNG_URL=http://localhost:8080 uvicorn app.main:app --reload --port 8000
```

> При первом старте backend скачает ML-модель (~120 MB) в `~/.cache/fastembed/`.
> Это займёт 1–2 мин, дальше модель берётся из кеша.

### Frontend (Node.js)

```bash
cd frontend
npm install          # устанавливает зависимости в node_modules/
npm run dev          # dev-сервер на http://localhost:5173
```

Запросы `frontend → /api/*` автоматически проксируются на `http://localhost:8000`
через `vite.config.js` (настройка `server.proxy`).

---

## 5. Парсеры

Все парсеры наследуют `BaseParser` и запускаются **параллельно** через `asyncio.gather()`.
Каждый использует общий `RateLimiter` и `fetch_with_retry` с backoff.

### 5.1 Wildberries

**Метод:** официальный публичный поисковый API WB — без авторизации, без ключей.

```
GET https://search.wb.ru/exactmatch/ru/common/v9/search
    ?query=ноутбук&resultset=catalog&limit=20&sort=popular
    &curr=rub&dest=-1275499   ← региональный dest-параметр
```

**Что получаем в JSON:**
- `data.products[]` — массив товаров
- `id` (nm_id) — используется для построения URL изображения по формуле `basket-{N}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/...`
- `priceU` — цена в копейках (делим на 100)
- `brand`, `name`, `colors`, `feedbacks`, `reviewRating`

**Регионализация:** параметр `dest` меняет цены и наличие. Маппинг: Москва → `-1257786`, СПБ → `-1275499`, и т.д.

**Почему WB работает надёжно:** единственный из трёх маркетплейсов, предоставляющий стабильный публичный JSON API без антибот-защиты.

---

### 5.2 Ozon — три стратегии

Ozon не имеет официального публичного API, поэтому используем каскад fallback-методов:

**Стратегия 1 — composer-API:**
```
GET https://api.ozon.ru/composer-api.bx/page/json/v2
    ?url=/search/?text={query}&layout_container=categorySearchMegapagination
Headers: x-o3-app-name: ozonfront, x-o3-app-version: 5.49.0
```
Возвращает `widgetStates` — JSON с виджетами страницы. Ищем ключи `searchResultsV2`, `tileGrid`.

**Стратегия 2 — entrypoint-API:**
```
GET https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2
    ?url=/search/?text={query}
```
Альтернативный endpoint того же формата, иногда менее защищён.

**Стратегия 3 — HTML + embedded JSON:**
Загружаем страницу `ozon.ru/search/?text=...`, ищем все `<script type="application/json">`,
рекурсивно обходим JSON-дерево функцией `_walk_for_products()` — находим объекты со структурой товара.

**Парсинг цены:** Ozon возвращает цену в нескольких форматах — `"1 299 ₽"` (строка), `1299` (число), `{"price": "1299"}` (объект). `_extract_price()` обрабатывает все варианты.

---

### 5.3 Яндекс Маркет — четыре метода извлечения

YM — наиболее агрессивно защищённый источник, использует антибот CloudFront + CAPTCHA. Применяем многоуровневый парсинг:

**Метод 1 — `__NEXT_DATA__`:**
```html
<script id="__NEXT_DATA__" type="application/json">{...}</script>
```
Современный YM (Next.js) встраивает все данные страницы в этот тег. Извлекаем через regex, затем рекурсивно ищем объекты с полями `name + price + id`.

**Метод 2 — 5 альтернативных regex-паттернов:**
```python
window.__NUXT__ = (function(){ return {...} }())  # старый Nuxt
window.__initialState__ = {...}                    # промежуточная версия
"offers": [...]                                   # offers-блок
"searchResults": {...}                            # блок результатов
```

**Метод 3 — BS4 DOM-парсинг:**
Ищем `<article>`, `[data-zone-name="productSnippet"]`, `[data-autotest-id~="product"]`.
Извлекаем название, цену, ссылку, картинку из DOM-структуры.

**Метод 4 — альтернативный URL с другим `pp` параметром:**
Иногда `?pp=18` возвращает менее защищённую версию страницы.

**Регионализация:** параметр `lr` — ID региона Яндекса (Москва=213, СПБ=2, и т.д.). Cookie `_region_id={id}` дополнительно.

---

### 5.4 Рунет (4-й нефиксированный источник)

**Принцип:** источник динамический — не один сайт, а результаты из всего Рунета.

**Шаг 1 — поиск через SearXNG:**
```
GET http://searxng:8080/search
    ?q=ноутбук+купить+цена&format=json&language=ru-RU
    &engines=duckduckgo,bing,google
```
SearXNG — self-hosted агрегатор (Docker-контейнер), запрашивает DuckDuckGo + Bing + Google параллельно и возвращает объединённый JSON. Не нужны API-ключи. Не фиксированный источник — список сайтов меняется с каждым запросом.

**Шаг 2 — fallback на DuckDuckGo HTML Lite:**
Если SearXNG недоступен — POST-запрос на `html.duckduckgo.com/html/`, парсим BS4.

**Шаг 3 — скрапинг найденных страниц:**
Для каждого URL из выдачи (исключая wb.ru, ozon.ru, market.yandex.ru):
- `trafilatura` или BS4 извлекают основной контент
- Цена ищется в порядке: `schema.org JSON-LD` → `itemprop="price"` → `og:price:amount` → regex паттерны `[\d\s]+₽`
- Изображение: `og:image` → `itemprop="image"` → первый `<img>`
- Характеристики: JSON-LD `@type: Product` → `brand`, `description`

**Фильтрация:** исключаем уже охваченные маркетплейсы — выдача содержит только «живые» магазины, интернет-ресурсы.

---

## 6. NLP-пайплайн

Весь NLP работает **без внешних API**, только локальные open-source инструменты.

```
Пользователь: «кросовки адидас»
                    │
         ┌──────────▼──────────┐
         │  _normalize(query)  │  lower, strip, collapse spaces
         └──────────┬──────────┘
                    │ «кросовки адидас»
         ┌──────────▼──────────┐
         │ TYPO_MAP lookup     │  O(1) прямой поиск
         └──────────┬──────────┘
                    │ не найдено
         ┌──────────▼───────────────┐
         │ pymorphy3 лемматизация   │  «кросовки» → «кросовка»
         │ (с @lru_cache на 4096)   │  «адидас» → «адидас»
         └──────────┬───────────────┘
                    │ «кросовка адидас»
         ┌──────────▼───────────────────┐
         │ rapidfuzz.process.extractOne │  82% порог Левенштейна
         │ по всем известным словам     │  «кросовка» → «кроссовки» ✓
         └──────────┬───────────────────┘
                    │ «кроссовки адидас»
         ┌──────────▼───────────────┐
         │  expand_synonyms()       │  «кроссовки» → {«sneakers», «кеды»}
         │  из SYNONYM_MAP          │  max 4 варианта
         └──────────┬───────────────┘
                    │
         ┌──────────▼────────────────────────┐
         │  primary_query: «кроссовки адидас» │
         │  search_variants: [«кроссовки      │
         │    адидас», «sneakers адидас»]      │
         │  was_corrected: true               │
         │  corrected: «кроссовки адидас»     │
         └────────────────────────────────────┘
```

### Компоненты NLP

#### `pymorphy3` v2.0.2 — морфологический анализатор
- **Тип:** rule-based, словарный (не нейросеть)
- **Размер:** ~15 MB (словари + правила)
- **Скорость:** < 0.5 мс/слово; результаты кешируются `@lru_cache(maxsize=4096)`
- **Задача:** привести слово к начальной форме (лемме) для нормализации запроса
- **Примеры:** `ноутбуки → ноутбук`, `резин → резина`, `принтеров → принтер`
- **Почему не ML:** pymorphy3 — стандарт де-факто для русской морфологии в open-source. Нейросетевые аналоги (spaCy ru, natasha) работают медленнее при том же качестве лемматизации

#### `rapidfuzz` v3.10.1 — нечёткое сравнение строк
- **Тип:** алгоритм (расстояние Левенштейна, Jaro-Winkler), не нейросеть
- **Размер:** ~1 MB
- **Скорость:** < 1 мс на сравнение
- **Задача:** исправить опечатки сравнением с известными словами
- **Порог:** 82% — найден эмпирически; ниже → ложные срабатывания, выше → пропуск реальных опечаток
- **Примеры:** `ноутбукк → ноутбук (95%)`, `кросовки → кроссовки (92%)`, `монитар → монитор (89%)`

#### Словарь синонимов (статический)
- **Тип:** хардкод, нет ML
- **Размер:** < 2 KB в памяти
- **Покрытие:** 3 категории хакатона — одежда (12 позиций), шины (4), оргтехника (14)
- **Структура:** `canonical_term → [synonym1, synonym2, ...]` + обратный индекс `synonym → canonical`

#### Словарь опечаток (TYPO_MAP)
- **Тип:** статический словарь, O(1) lookup
- **Покрытие:** 15 наиболее частых опечаток в категориях товаров
- **Назначение:** быстрая обработка самых частых ошибок без fuzzy-matching

---

## 7. ML-ранжирование

После получения результатов от всех парсеров каждый список товаров переранжируется нейросетью.

### Модель: `paraphrase-multilingual-MiniLM-L12-v2`

| Параметр | Значение |
|----------|---------|
| Архитектура | MiniLM (дистилляция из BERT) |
| Слоёв трансформера | 12 |
| Размерность эмбеддинга | 384 |
| Формат инференса | ONNX (через fastembed) |
| Размер файла | ~120 MB |
| Поддерживаемые языки | 50+, включая русский |
| Инференс на CPU | ~30–80 мс / батч 20 товаров |
| RAM при загрузке | ~300 MB |

### Почему fastembed, а не sentence-transformers

`sentence-transformers` — стандартная обёртка для этой модели, но требует **PyTorch (~700 MB)**.
`fastembed` использует тот же ONNX-экспорт модели через **onnxruntime (~50 MB)** — то же качество, в 14× меньше зависимостей. Это прямо соответствует требованию хакатона «лёгкие решения».

### Алгоритм ранжирования

```python
# 1. Векторизация (один батч = запрос + все товары источника)
texts = [query] + [item.title for item in items]
embeddings = model.embed(texts)   # shape: (N+1, 384)

# 2. Косинусное сходство
query_vec  = embeddings[0]         # вектор запроса
item_vecs  = embeddings[1:]        # векторы заголовков товаров
scores     = [cosine(query_vec, v) for v in item_vecs]  # float 0..1

# 3. Сортировка по убыванию score
ranked = sorted(zip(items, scores), key=lambda x: x[1], reverse=True)
```

### Пример

```
Запрос: «зимние шины 205/55 R16»

До ранжирования:               После ранжирования:
  Nokian Tyres летние 195/65    Nokian Hakkapeliitta R5 205/55 R16  score=0.91
  Michelin 205/55 R16 зимние    Michelin X-Ice North 4 205/55 R16   score=0.89
  Nokian Hakkapeliitta 205/55   Yokohama IceGuard IG65 205/55 R16   score=0.87
  Куртка зимняя Adidas           Nokian Tyres летние 195/65          score=0.31
  Yokohama IceGuard 205/55      Куртка зимняя Adidas                score=0.04
```

### Жизненный цикл модели

```
docker compose build
  └─▶ Dockerfile: python -c "from fastembed import TextEmbedding; ..."
        └─▶ Модель скачивается и кешируется в /opt/fastembed_cache
              └─▶ Бейкается в Docker-слой (нет задержки на первый запрос)

docker compose up
  └─▶ FastAPI lifespan(startup)
        └─▶ warmup(): list(model.embed(["прогрев"]))
              └─▶ Модель готова ещё до первого HTTP-запроса

GET /api/search?q=ноутбук
  └─▶ asyncio.gather(wb, ozon, ym, runet) → параллельный парсинг
        └─▶ run_in_executor(rank_items, query, items) × 4 источника
              └─▶ Ре-ранкинг в thread pool, не блокирует event loop
```

### Graceful degradation

Если `fastembed` по какой-то причине не загрузился — `rank_items()` возвращает исходный список без изменений. Сервис продолжает работать, ранжирование просто отключается. Поле `relevance_score` в ответе будет `null`.

---

## 8. Rate Limiting

Без ограничений частоты запросов Ozon и Яндекс Маркет возвращают 429 или CAPTCHA.

### Задержки по источникам

| Источник | Задержка между запросами | Причина |
|----------|--------------------------|---------|
| **Wildberries** | нет (официальный API) | публичный endpoint, нет rate limiting |
| **Ozon** | 1.5–5.0 сек (случайная) | bot-detection: 429 при > 1 req/s с одного IP |
| **Яндекс Маркет** | 2.0–4.5 сек (случайная) | агрессивная защита CloudFront + CAPTCHA при > 0.5 req/s |
| **Рунет (SearXNG)** | 1.5–3.5 сек/сайт | этика: не нагружаем конечные сайты |

### Дополнительные механизмы

**Ротация User-Agent** — 5 строк реальных браузеров (Chrome 124, Firefox 125, Safari 17.4, и др.):
```python
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0...",
    "Mozilla/5.0 (X11; Linux x86_64) Chrome/123.0.0.0...",
    ...
]
```

**Реалистичные заголовки браузера:**
```http
Accept: text/html,application/xhtml+xml,...
Accept-Language: ru-RU,ru;q=0.9,en-US;q=0.8
Sec-Fetch-Dest: document
Sec-Fetch-Mode: navigate
Sec-CH-UA: "Chromium";v="124"
```

**Экспоненциальный backoff при 429:**
```
attempt 1: ждём 3–6 сек
attempt 2: ждём 9–12 сек
attempt 3: ждём 27–30 сек → возвращаем пустой список
```

**`asyncio.Semaphore(3)`** — не более 3 одновременных запросов к одному домену глобально.

**Итоговое время поиска:** 5–15 сек в зависимости от откликов источников.

---

## 9. API-контракты

### `GET /api/search`

Основной эндпоинт. Запускает 4 парсера параллельно, применяет NLP и ML-ранжирование.

**Query Parameters:**

| Параметр | Тип | Обязателен | Дефолт | Ограничения |
|----------|-----|------------|--------|-------------|
| `q` | string | ✅ | — | минимум 2 символа |
| `region` | string | ❌ | `Москва` | см. таблицу регионов ниже |
| `limit` | integer | ❌ | `10` | от 1 до 30 |

**Пример запроса:**
```http
GET /api/search?q=ноутбукк&region=Санкт-Петербург&limit=10
```

**Ответ `200 OK` — `SearchResponse`:**
```json
{
  "original_query": "ноутбукк",
  "corrected_query": "ноутбук",
  "was_corrected": true,
  "search_variants": ["ноутбук", "laptop", "нетбук"],
  "region": "Санкт-Петербург",
  "total_items": 38,
  "results": [
    {
      "source": "Яндекс Маркет",
      "total_found": 10,
      "price_min": 12990.0,
      "price_max": 89990.0,
      "price_avg": 45320.5,
      "items": [
        {
          "title": "Ноутбук ASUS VivoBook 15 X1504VA",
          "price": 34990.0,
          "currency": "RUB",
          "image_url": "https://avatars.mds.yandex.net/...",
          "product_url": "https://market.yandex.ru/product/...",
          "source": "Яндекс Маркет",
          "characteristics": {
            "Бренд": "ASUS",
            "Процессор": "Intel Core i5-1335U",
            "ОЗУ": "8 ГБ"
          },
          "rating": 4.7,
          "reviews_count": 234,
          "relevance_score": 0.8921
        }
      ]
    },
    {
      "source": "Ozon",
      "total_found": 9,
      "price_min": 11500.0,
      "price_max": 92000.0,
      "price_avg": 41200.0,
      "items": ["..."]
    },
    {
      "source": "Wildberries",
      "total_found": 10,
      "price_min": 15990.0,
      "price_max": 75000.0,
      "price_avg": 38500.0,
      "items": ["..."]
    },
    {
      "source": "Интернет (Рунет)",
      "total_found": 6,
      "price_min": 28000.0,
      "price_max": 85000.0,
      "price_avg": 52000.0,
      "items": [
        {
          "title": "Ноутбук ASUS VivoBook — купить",
          "price": 42000.0,
          "currency": "RUB",
          "image_url": "https://citilink.ru/img/...",
          "product_url": "https://citilink.ru/product/...",
          "source": "Рунет (citilink.ru)",
          "characteristics": {
            "Бренд": "ASUS",
            "Описание": "15.6\", Intel Core i5, 8 ГБ RAM..."
          },
          "rating": null,
          "reviews_count": null,
          "relevance_score": 0.7634
        }
      ]
    }
  ]
}
```

**Схема `ProductItem`:**

| Поле | Тип | Источник | Описание |
|------|-----|---------|----------|
| `title` | string | все | наименование товара |
| `price` | float \| null | все | цена в рублях; null если не найдена |
| `currency` | string | — | всегда `"RUB"` |
| `image_url` | string \| null | все | прямая ссылка на изображение |
| `product_url` | string | все | ссылка на страницу товара на источнике |
| `source` | string | — | `"Яндекс Маркет"`, `"Ozon"`, `"Wildberries"`, `"Рунет (домен)"` |
| `characteristics` | object | все | словарь `{поле: значение}`, например `{"Бренд": "ASUS"}` |
| `rating` | float \| null | WB, YM | рейтинг 0–5; Ozon и Рунет чаще null |
| `reviews_count` | int \| null | WB, YM | количество отзывов |
| `relevance_score` | float \| null | ML | косинусное сходство с запросом 0–1; null если ML отключён |

**Схема `SearchResult` (один источник):**

| Поле | Тип | Описание |
|------|-----|----------|
| `source` | string | название источника |
| `total_found` | int | количество найденных товаров |
| `price_min` | float \| null | минимальная цена среди найденных |
| `price_max` | float \| null | максимальная цена |
| `price_avg` | float \| null | средняя цена (округлена до 2 знаков) |
| `items` | list[ProductItem] | список товаров, отсортированных по relevance_score |

**Поддерживаемые регионы:**

| Название | Параметр в запросе | WB dest | YM lr |
|----------|-------------------|---------|-------|
| Москва | `Москва` | -1257786 | 213 |
| Санкт-Петербург | `Санкт-Петербург` / `СПБ` | -1275499 | 2 |
| Новосибирск | `Новосибирск` | -364632 | 65 |
| Екатеринбург | `Екатеринбург` | -1198055 | 54 |
| Казань | `Казань` | -2133466 | 43 |
| Нижний Новгород | `Нижний Новгород` | -2096398 | 47 |
| Краснодар | `Краснодар` | -3520000 | 35 |

---

### `GET /api/health`

```http
GET /api/health
→ 200 OK
{"status": "ok"}
```

Используется Docker healthcheck для определения готовности backend.

---

## 10. Переменные окружения

| Переменная | Дефолт | Описание |
|------------|--------|---------|
| `SEARXNG_URL` | `http://localhost:8080` | адрес SearXNG внутри Docker-сети (`http://searxng:8080`) |
| `FASTEMBED_CACHE_PATH` | `~/.cache/fastembed` | путь кеша ML-модели; в Docker → `/opt/fastembed_cache` |
| `LOG_LEVEL` | `INFO` | уровень логирования uvicorn/FastAPI |
| `PYTHONUNBUFFERED` | `1` | немедленный вывод логов в Docker |

---

## 11. BPMN-схема

```
[Начало] Пользователь вводит запрос и выбирает регион
    │
    ▼
[Задача] Нормализация запроса
    │  pymorphy3: лемматизация каждого слова
    │  TYPO_MAP: прямой lookup опечаток
    │  rapidfuzz: нечёткое исправление (порог 82%)
    │
    ▼
[Шлюз] Запрос исправлен?
    │ Да ──▶ [Задача] Показать баннер «Вы искали: X, показываем: Y»
    │
    ▼
[Задача] Расширение синонимами (SYNONYM_MAP)
    │  «резина» → «шины», «покрышки»; max 4 варианта
    │
    ▼
[Параллельный шлюз] ─────────────────────────────────────
    │ primary_query передаётся всем парсерам одновременно
    │
    ├──▶ [Задача] WildberriesParser
    │       search.wb.ru/exactmatch/v9/search (JSON API)
    │       Регионализация через dest-параметр
    │
    ├──▶ [Задача] OzonParser
    │       Попытка 1: composer-API
    │       Попытка 2: entrypoint-API
    │       Попытка 3: HTML + embedded JSON
    │
    ├──▶ [Задача] YandexMarketParser
    │       Попытка 1: __NEXT_DATA__ (Next.js)
    │       Попытка 2: 5 regex-паттернов JSON
    │       Попытка 3: BS4 DOM-парсинг
    │
    └──▶ [Задача] RunetParser
            SearXNG → DuckDuckGo + Bing + Google → JSON
            Fallback: DuckDuckGo HTML Lite
            Скрапинг страниц: schema.org → og:price → regex
    │
    ▼
[Соединяющий шлюз] asyncio.gather() — ждём все 4 результата
    │
    ▼
[Задача] ML-ранжирование (fastembed + MiniLM-L12-v2)
    │  Для каждого источника отдельно:
    │  encode(query + titles) → cosine_similarity → sort desc
    │  Записываем relevance_score в каждый товар
    │
    ▼
[Задача] Агрегация и формирование ответа
    │  price_min / price_max / price_avg по каждому источнику
    │  Сериализация через Pydantic → JSON
    │
    ▼
[Задача] Frontend рендеринг
    │  CorrectionBanner (если was_corrected=true)
    │  StatsBar (сводка по источникам)
    │  SourceSection × 4 (карточки с фото, ценой, ссылкой)
    │
    ▼
[Конец]
```

**Прикладное ПО по слоям:**

| Слой | ПО |
|------|----|
| Веб-сервер | Nginx alpine |
| UI | React 18, Vite 5, CSS Modules, axios |
| API | FastAPI 0.115, uvicorn 0.32, Pydantic 2 |
| HTTP-клиент | httpx 0.27 (async) |
| Парсинг HTML | BeautifulSoup4 4.12, lxml 5.3, trafilatura 1.12 |
| NLP | pymorphy3 2.0, rapidfuzz 3.10, SYNONYM_MAP |
| ML | fastembed 0.4, ONNX Runtime, MiniLM-L12-v2 |
| 4-й источник | SearXNG (Docker, AGPL-3.0) |
| Оркестрация | Docker Compose V2, healthchecks |

---

## 12. Ограничения

1. **Яндекс Маркет и Ozon** используют антибот-защиту. При смене IP-адреса или высокой нагрузке возможны пустые результаты. Wildberries и Рунет (SearXNG) работают стабильно.

2. **Время ответа 5–15 сек** — следствие обязательных задержек rate limiting. Первый запрос после старта контейнеров может занять до 20 сек (cold start парсеров + прогрев SearXNG).

3. **ML-модель скачивается при `docker compose build`** (+2–3 мин к первой сборке, +~120 MB к образу). Без интернета сборка упадёт.

4. **SearXNG** агрегирует внешние поисковые движки — зависит от их доступности. При недоступности — fallback на DuckDuckGo HTML.

5. **Авторизация не реализована** — соответствует условиям хакатона («не требуется делать авторизацию»).

6. **Сохранение истории не реализовано** — соответствует условиям хакатона.
