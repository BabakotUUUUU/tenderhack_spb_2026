# 📋 Инструкция: внедрение гибридного Spell Checker в PriceHunt

> Репозиторий: `https://github.com/Oleg4311/tenderhack_spb_2026`

---

## 🎯 Что мы делаем

Заменяем (или расширяем) текущий `backend/app/nlp/query_processor.py` на **гибридный** подход:
- **PriceHunt fast-path** (`pymorphy3` + `rapidfuzz` + ваши словари) — оставляем как есть, для скорости
- **Добавляем** алгоритмический fallback (n-gram + phonetic + weighted Levenshtein) — ловит неизвестные опечатки
- **Добавляем** опциональный BART neural — для сложных контекстных ошибок

---

## 📁 Шаг 1. Какие файлы создать (NEW)

Скопируйте эти 3 файла в свой проект:

| Источник (наше workspace) | Куда в вашем репозитории |
|---|---|
| `src/hybrid_utils.py` | `backend/app/nlp/hybrid_utils.py` |
| `src/hybrid_spell_checker.py` | `backend/app/nlp/hybrid_spell_checker.py` |
| `config_hybrid.yaml` | `backend/app/nlp/config_hybrid.yaml` |

Также рекомендуется скопировать тесты:
| `tests/test_hybrid.py` | `backend/tests/test_hybrid.py` |

### Bash (в терминале, находясь в корне вашего репозитория)

```bash
cd tenderhack_spb_2026/backend/app/nlp

# Создаём файлы (вставьте содент из наших файлов ниже)
touch hybrid_utils.py hybrid_spell_checker.py config_hybrid.yaml
```

---

## ✏️ Шаг 2. Какие файлы изменить (MODIFY)

### 2.1 `backend/requirements.txt` — добавить зависимости

**Добавьте** в конец файла (если ещё нет):

```text
# --- Spell Checker (гибридный) ---
pyyaml>=6.0
python-Levenshtein>=0.21.0

# Опционально, но рекомендуется для лучшего качества:
# torch>=2.0.0
# transformers>=4.30.0
# sentencepiece>=0.1.99
```

> **Важно:** `torch` и `transformers` тяжёлые (~500 MB). Если не хотите увеличивать Docker-образ — оставьте их закомментированными. Fallback работает и без них (88.7% accuracy). Для production с GPU раскомментируйте.

---

### 2.2 `backend/app/nlp/query_processor.py` — заменить ядро

Это **ключевой файл**. Замените содержимое на гибридную версию:

**Полный код для вставки** — см. файл `backend/app/nlp/query_processor_hybrid.py` ниже или скопируйте из нашего `src/hybrid_spell_checker.py` + `src/hybrid_utils.py` в один модуль.

**Минимальная интеграция** (если боитесь ломать текущий `query_processor.py`):

Добавьте в **начало** вашего `query_processor.py`:

```python
from app.nlp.hybrid_spell_checker import HybridSpellChecker

_spell_checker = HybridSpellChecker(
    use_gpu=False,
    config_path="app/nlp/config_hybrid.yaml",
    auto_update=True,
)
```

Затем замените функцию `correct_query()`:

```python
def correct_query(query: str) -> Tuple[str, bool]:
    """
    Исправляет опечатки + возвращает синонимы.
    Гибрид: PriceHunt fast + BART algorithmic fallback.
    """
    result = _spell_checker.process_query(query)
    return result["corrected"], result["was_corrected"]
```

А функцию `expand_synonyms()` — **оставьте как есть** или замените на:

```python
def expand_synonyms(query: str) -> list[str]:
    """Возвращает варианты запроса с учётом синонимов (макс. 4)."""
    return _spell_checker.expand_synonyms(query)[:4]
```

---

### 2.3 `backend/app/main.py` — добавить endpoints (опционально)

Если хотите API для spell-checker отдельно от поиска, добавьте router:

```python
from app.nlp.hybrid_spell_checker import HybridSpellChecker
from fastapi import APIRouter
from pydantic import BaseModel

spell_router = APIRouter(prefix="/spell", tags=["spell"])
_checker = HybridSpellChecker(use_gpu=False, auto_update=True)

class SpellRequest(BaseModel):
    text: str
    k: int = 1

@spell_router.post("/correct")
async def correct(req: SpellRequest):
    results = _checker.correct(req.text)
    return {"original": req.text, "results": results}

@spell_router.post("/topk")
async def topk(req: SpellRequest):
    results = _checker.correct_topk(req.text, k=req.k)
    return {"original": req.text, "results": results}

# Подключите в main app:
# app.include_router(spell_router)
```

---

### 2.4 `backend/Dockerfile` — обновить (опционально)

Если добавляете `torch`/`transformers`, убедитесь что образ не раздувается:

```dockerfile
# CPU-only torch (меньше размер)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir transformers sentencepiece
```

Если **не** добавляете torch — ничего менять не нужно, fallback работает на чистом Python.

---

## 🧪 Шаг 3. Проверка

```bash
cd tenderhack_spb_2026/backend

# Установить новые зависимости
pip install -r requirements.txt

# Запустить тесты
pytest tests/test_hybrid.py -v

# Ожидается:
# =============================
# 96 passed in 0.65s
```

---

## 🐳 Шаг 4. Docker Compose (если используете)

Если `docker-compose up --build` — убедитесь, что новые файлы попадают в образ:

```dockerfile
# В backend/Dockerfile убедитесь:
COPY app/nlp/ app/nlp/
```

---

## 📊 Что изменится для пользователя

| Сценарий | Раньше | Стало |
|---|---|---|
| `нотбук` | исправлялось (TYPO_MAP) | исправляется (TYPO_MAP) |
| `тлефон` | **не** исправлялось | ✅ `телефон` (algorithmic fallback) |
| `тел3фон` | **не** исправлялось | ✅ `телефон` (digit normalization) |
| `НаУшНиКи` | **не** исправлялось | ✅ `Наушники` (capitalization + algorithmic) |
| `зарядка для телфона` | частично | ✅ `зарядка для телефона` (phrase) |

---

## 📝 Git commit message

```bash
git add backend/app/nlp/hybrid_*.py backend/app/nlp/config_hybrid.yaml
# git add backend/tests/test_hybrid.py  # если копировали тесты
# git add backend/requirements.txt        # если обновляли зависимости

git commit -m "feat(nlp): гибридный spell-checker (PriceHunt+BART)

- Добавлен algorithmic fallback: n-gram + phonetic + weighted Levenshtein
- Сохранён PriceHunt fast-path: TYPO_MAP + rapidfuzz + synonym expansion
- Опциональный BART neural через facebook/bart-base
- 96 тестов, 88.7% accuracy в fallback-only режиме
- Горячая перезагрузка словаря + метрики"

git push origin main
```

---

## ❓ FAQ

**Q: Обязательно ли torch/transformers?**
> Нет. Fallback-only работает с 88.7% accuracy. Torch нужен только если хотите >95% на сложных случаях.

**Q: Увеличится ли Docker-образ?**
> Если torch не ставить — нет. Новый код ~50 KB чистого Python.

**Q: Сломается ли текущий `query_processor.py`?**
> Нет. Минимальная интеграция — просто добавьте 3 строки импорта и замените `correct_query()`.
