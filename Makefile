.PHONY: help up down logs build migrate test fmt lint shell seed clean

help:
	@echo "Autonomous Operational Intelligence Layer"
	@echo ""
	@echo "  make up          — start the full stack (postgres, redis, backend, worker, frontend)"
	@echo "  make down        — stop the stack"
	@echo "  make logs        — tail logs for all services"
	@echo "  make build       — rebuild backend image"
	@echo "  make migrate     — run alembic migrations"
	@echo "  make test        — run pytest inside the backend container"
	@echo "  make fmt         — format backend code with ruff"
	@echo "  make lint        — lint backend code"
	@echo "  make shell       — open a shell inside the backend container"
	@echo "  make seed        — load the example transcript through the API"
	@echo "  make secure-env  — re-tighten .env permissions to 0600"
	@echo "  make clean       — stop stack and remove volumes (DESTRUCTIVE)"

up: .env
	docker compose up -d --build

# Create .env from the template on first run AND lock it down to owner-only
# (0600). Without this it would inherit the umask default (often 0644 → world-
# readable), which leaks the key to any process running as another user.
.env:
	cp .env.example .env
	chmod 600 .env
	@echo ""
	@echo "  → Created .env (mode 600). Edit it to add ANTHROPIC_API_KEY."
	@echo ""

# Re-tighten perms in case .env was created by some other tool.
secure-env:
	@chmod 600 .env && echo ".env permissions tightened to 600 (owner-only)."

down:
	docker compose down

logs:
	docker compose logs -f

build:
	docker compose build backend

migrate:
	docker compose exec backend alembic upgrade head

test:
	docker compose exec backend pytest -v

fmt:
	docker compose exec backend ruff format app tests

lint:
	docker compose exec backend ruff check app tests

shell:
	docker compose exec backend /bin/sh

seed:
	@curl -sS -X POST http://localhost:8000/api/transcripts \
		-H "Content-Type: application/json" \
		-d @examples/sample_transcript.json | python3 -m json.tool

clean:
	docker compose down -v
