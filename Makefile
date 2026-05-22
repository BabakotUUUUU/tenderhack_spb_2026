.PHONY: up down build restart logs logs-backend logs-frontend status clean dev-backend dev-frontend

# ── Docker-compose (основной способ запуска) ────────────────────────────────

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

logs-searxng:
	docker compose logs -f --tail=100 searxng

status:
	docker compose ps

# Полная пересборка и запуск
fresh: down build up
	@echo "Стек запущен:"
	@echo "  Frontend:  http://localhost:3000"
	@echo "  Backend:   http://localhost:8000/docs"
	@echo "  SearXNG:   http://localhost:8080"

# ── Локальная разработка (без Docker) ──────────────────────────────────────

dev-backend:
	cd backend && \
	  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

dev-frontend:
	cd frontend && npm run dev

install-backend:
	cd backend && pip install -r requirements.txt

install-frontend:
	cd frontend && npm install

# ── Утилиты ────────────────────────────────────────────────────────────────

clean:
	docker compose down -v --remove-orphans
	docker system prune -f

# Проверка здоровья сервисов
health:
	@curl -s http://localhost:8000/api/health | python3 -m json.tool
	@echo "SearXNG:"; curl -s http://localhost:8080/healthz
