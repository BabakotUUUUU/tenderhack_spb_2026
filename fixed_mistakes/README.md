# Гибридный Spell Checker — PriceHunt + BART

Гибридная система исправления опечаток в русских поисковых запросах, объединяющая **скорость PriceHunt** (`pymorphy3` + `rapidfuzz` + ручные словари) с **мощью BART** (нейросетевой seq2seq + фонетические алгоритмы + взвешенное расстояние Левенштейна).

> **Цель**: исправлять опечатки за **< 5 мс** для 80% простых случаев, и **< 20 мс** для сложных через алгоритмический fallback — без обязательной зависимости от `torch`/`transformers`.

---

## 🏗️ Архитектура

```
Пользовательский запрос
        │
        ▼
┌─────────────────────────────────────────┐
│  1. PriceHunt Fast-Path  (< 5 мс)       │
│     • EN→RU keyboard layout fix         │
│     • Tire pattern (205/55 R16)         │
│     • TYPO_MAP O(1) lookup              │
│     • rapidfuzz (порог 82%)             │
│     • pymorphy3 лемматизация            │
│     • SYNONYM_MAP expansion             │
└─────────────────────────────────────────┘
        │ (если не исправлено)
        ▼
┌─────────────────────────────────────────┐
│  2. BART Algorithmic Fallback           │
│     • N-gram Jaccard similarity         │
│     • Phonetic index (Soundex/Metaphone)│
│     • Weighted Levenshtein (keyboard)   │
│     • Digit/symbol normalization        │
│     • Hot-reload dictionary             │
└─────────────────────────────────────────┘
        │ (если torch доступен)
        ▼
┌─────────────────────────────────────────┐
│  3. BART Neural (опционально)           │
│     • facebook/bart-base seq2seq        │
│     • Text infilling (<mask>)           │
│     • Sentence permutation (sept)     │
└─────────────────────────────────────────┘
        │
        ▼
   Исправленный запрос + синонимы + метрики
```

---

## 📦 Установка

### Базовые зависимости (обязательные)

```bash
pip install pyyaml pymorphy3 python-Levenshtein
```

### Опциональные зависимости

| Компонент | Команда установки | Зачем |
|---|---|---|
| **PriceHunt fast-path** | `pip install rapidfuzz` | Fuzzy matching на 82% пороге |
| **BART neural** | `pip install torch transformers` | Seq2seq коррекция через BART |
| **API** | `pip install fastapi uvicorn` | REST API сервис |
| **Тесты** | `pip install pytest pytest-asyncio` | Юнит-тесты |
| **Логирование** | `pip install structlog` | Структурированные логи |

### Полный набор (для разработки)

```bash
pip install -r requirements.txt
```

---

## 🚀 Быстрый старт

### 1. Простая коррекция

```python
from src.hybrid_spell_checker import HybridSpellChecker

checker = HybridSpellChecker(
    use_gpu=False,           # GPU опционально
    auto_update=True,      # Авторасширение словаря
)

# Один запрос
result = checker.correct("тлефон")
print(result)  # ['тлефон', 'телефон']

# Топ-3 варианта
result = checker.correct_topk("мыш", k=3)
print(result)  # ['мышь', ...]

# Пакетная обработка (sync)
results = checker.correct_batch(["тлефон", "компютер", "наушнеки"])

# Пакетная обработка (async)
import asyncio
results = asyncio.run(checker.correct_batch_async(["тлефон", "компютер"], k=2))
```

### 2. Полный pipeline (как в PriceHunt)

```python
processed = checker.process_query("нотбук")
print(processed)
```

**Вывод:**
```json
{
  "original": "нотбук",
  "corrected": "ноутбук",
  "was_corrected": true,
  "search_variants": ["ноутбук", "laptop", "лэптоп"],
  "used_synonyms": {"ноутбук": ["laptop", "лэптоп", "нетбук", "ультрабук"]},
  "expanded_queries": ["ноутбук", "laptop", "лэптоп"],
  "primary_query": "ноутбук",
  "category": "office_tech"
}
```

### 3. Демонстрация

```bash
python demo_hybrid.py
```

---

## 🌐 REST API (FastAPI)

### Запуск

```bash
uvicorn src.api:app --host 0.0.0.0 --port 8000
```

### Endpoints

| Метод | Endpoint | Описание |
|---|---|---|
| `POST` | `/correct` | Одиночная коррекция |
| `POST` | `/correct/topk` | Топ-k вариантов |
| `POST` | `/correct/batch` | Пакетная коррекция (async) |
| `POST` | `/synthetic` | Генерация synthetic (noisy, clean) пар |
| `GET`  | `/health` | Health check |
| `POST` | `/dict/import` | Импорт пользовательского словаря |
| `GET`  | `/dict/export` | Экспорт текущего словаря |

### Примеры запросов

```bash
# Коррекция
curl -X POST http://localhost:8000/correct \
  -H "Content-Type: application/json" \
  -d '{"text": "тлефон"}'

# Топ-3 варианта
curl -X POST http://localhost:8000/correct/topk \
  -H "Content-Type: application/json" \
  -d '{"text": "мыш", "k": 3}'

# Пакетная обработка
curl -X POST http://localhost:8000/correct/batch \
  -H "Content-Type: application/json" \
  -d '{"texts": ["тлефон", "компютер"], "k": 2}'

# Synthetic noise generation
curl -X POST http://localhost:8000/synthetic \
  -H "Content-Type: application/json" \
  -d '{"clean_texts": ["телефон нокиа", "ноутбук для игр"], "noise_prob": 0.8}'
```

---

## 🧪 Тестирование

```bash
# Запуск всех тестов
pytest tests/test_hybrid.py -v

# С покрытием (опционально)
pytest tests/test_hybrid.py -v --cov=src

# Результат ожидается:
# =============================
# 96 passed in 0.65s
```

### Тестовый набор

- **71 параметризованный пример** из `data/typos_dataset.py`
- Категории: keyboard (29), omission (3), insertion (4), transposition (4), mixed (9), phonetic (6), context (8), capitalization (3), correct (5)

---

## 📁 Структура проекта

```
.
├── config_hybrid.yaml          # Конфигурация (модели, fallback, API)
├── requirements.txt            # Зависимости
├── demo_hybrid.py              # Демонстрация
│
├── src/
│   ├── hybrid_spell_checker.py # Основной класс HybridSpellChecker
│   ├── hybrid_utils.py         # Утилиты (SYNONYM_MAP, TYPO_MAP, phonetic, metrics)
│   ├── api.py                  # FastAPI сервис
│   ├── keyboard_utils.py       # Клавиатурное расстояние (legacy)
│   ├── phonetic.py             # Фонетика (legacy)
│   ├── spell_checker.py        # BART-only версия (legacy)
│   └── utils.py                # Базовые утилиты (legacy)
│
├── tests/
│   ├── test_hybrid.py          # Гибридные тесты (96 шт.)
│   └── benchmark.py            # Бенчмарки производительности
│
└── data/
    ├── typos_dataset.py          # 71 тестовый пример
    └── hybrid_dict.txt           # Автообновляемый словарь
```

---

## ⚙️ Конфигурация

Редактируйте `config_hybrid.yaml`:

```yaml
pricehunt:
  rapidfuzz_threshold: 82      # Порог fuzzy match (%)
  max_variants: 4                # Макс. число синонимов
  enable_tire_pattern: true      # Распознавать шины (205/55 R16)
  enable_keyboard_layout_fix: true  # EN->RU раскладка

fallback:
  max_levenshtein_distance: 5    # Макс. расстояние для кандидатов
  auto_update: true              # Автодобавление слов из запросов
  hot_reload_interval: 300       # Интервал hot-reload словаря (сек)

models:
  primary:
    name: "facebook/bart-base"   # Основная seq2seq модель
    use_gpu: true
    max_length: 128
    num_beams: 5
```

---

## 📊 Метрики

Гибридный подход (fallback-only, без torch):

| Метрика | Значение |
|---|---|
| **Accuracy** | **88.7%** |
| **Precision** | **88.2%** |
| **Recall** | **100%** |
| **F1** | **93.8%** |
| **Latency (fast-path)** | **< 5 мс** |
| **Latency (algorithmic)** | **< 20 мс** |

С подключённым BART (`torch` + `transformers`) ожидается **> 95% accuracy** на сложных контекстных опечатках.

---

## 🔗 Интеграция в PriceHunt

Замените вызов `process_query()` в `backend/app/nlp/query_processor.py`:

```python
# Было:
from app.nlp.query_processor import process_query

# Стало:
from src.hybrid_spell_checker import HybridSpellChecker

_spell_checker = HybridSpellChecker(auto_update=True)

def process_query(raw_query: str):
    return _spell_checker.process_query(raw_query)
```

Или используйте `correct()` для lightweight-интеграции:

```python
corrected = checker.correct(user_query)[-1]  # последний элемент — исправленный
```

---

## 📝 Лицензия

MIT — свободно для использования в проекте PriceHunt и любом другом.
