.PHONY: up down build restart logs logs-backend logs-frontend status clean dev-backend dev-frontend test

# ── Docker (основной способ запуска) ────────────────────────────────────────

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build --no-cache

restart:
	docker compose restart

logs:
	docker compose logs -f --tail=100

logs-backend:
	docker compose logs -f --tail=100 backend

logs-frontend:
	docker compose logs -f --tail=100 frontend

status:
	docker compose ps

fresh: down build up
	@echo ""
	@echo "  Frontend: http://localhost:3000"
	@echo "  Backend:  http://localhost:8000/docs"
	@echo ""

# ── Локальная разработка (без Docker) ──────────────────────────────────────

dev-backend:
	cd backend && CRAWLER_DB_PATH=/tmp/runet_index.db \
	  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

dev-frontend:
	cd frontend && npm run dev

install-backend:
	cd backend && pip install -r requirements.txt

install-frontend:
	cd frontend && npm install

# ── Тесты ──────────────────────────────────────────────────────────────────

test:
	cd backend && python -m pytest tests/ -v

test-nlp:
	cd backend && python -m pytest tests/test_nlp.py -v

test-extractor:
	cd backend && python -m pytest tests/test_extractor.py -v

# ── Утилиты ────────────────────────────────────────────────────────────────

clean:
	docker compose down -v --remove-orphans
	docker system prune -f

health:
	@curl -s http://localhost:8000/api/health | python3 -m json.tool

# Проверка что товары с опечаткой исправляются
demo-typo:
	@curl -s "http://localhost:8000/api/search?q=нотбук&region=Москва" | python3 -m json.tool | grep -E "was_corrected|corrected_query"

# Проверка 4-го источника
demo-runet:
	@curl -s "http://localhost:8000/api/search?q=шины+R16&region=Москва" | python3 -m json.tool | python3 -c "import sys,json; d=json.load(sys.stdin); [print(r['source'],r['total_found']) for r in d.get('results',[])]"
