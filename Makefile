.PHONY: help install dev-install run dev lint format test clean \
        docker-build docker-up docker-down docker-logs docker-restart \
        gen-certs db-migrate pi-setup pi-deploy

# ── Variables ──────────────────────────────────────────────────────────────────
DOCKER_COMPOSE = docker compose
POETRY = poetry run

help:
	@echo "Investment Assistant — Available Commands"
	@echo "=========================================="
	@echo ""
	@echo "  Development"
	@echo "  -----------"
	@echo "  make install       Install Python dependencies"
	@echo "  make run           Run locally (requires .env with local PostgreSQL)"
	@echo "  make dev           Run with auto-reload"
	@echo "  make lint          Ruff lint + mypy"
	@echo "  make format        Format code with Ruff"
	@echo "  make test          Run test suite"
	@echo ""
	@echo "  Docker (Raspberry Pi 5)"
	@echo "  ----------------------"
	@echo "  make docker-build  Build the app image"
	@echo "  make docker-up     Start all services (detached)"
	@echo "  make docker-down   Stop all services"
	@echo "  make docker-logs   Tail all logs"
	@echo "  make docker-restart  Restart the app container"
	@echo "  make gen-certs     Generate self-signed TLS cert for Nginx"
	@echo "  make pi-setup      Prepare a fresh Raspberry Pi"
	@echo "  make pi-deploy     Validate config, build, and start services"
	@echo ""

# ── Python / Dev ───────────────────────────────────────────────────────────────
install:
	poetry install --only main

dev-install:
	poetry install

run:
	$(POETRY) uvicorn src.app:app --host 0.0.0.0 --port 8000

dev:
	$(POETRY) uvicorn src.app:app --host 0.0.0.0 --port 8000 --reload

lint:
	$(POETRY) ruff check src/
	$(POETRY) mypy --follow-imports=skip src/

format:
	$(POETRY) ruff format src/

test:
	$(POETRY) pytest

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -type f -name "*.pyc" -delete
	rm -rf .pytest_cache/ .mypy_cache/ .ruff_cache/

# ── Docker ─────────────────────────────────────────────────────────────────────
docker-build:
	$(DOCKER_COMPOSE) build --no-cache app

docker-up:
	$(DOCKER_COMPOSE) up -d
	@echo "Services started. App is available only through the WireGuard VPN at https://10.8.0.1"

docker-down:
	$(DOCKER_COMPOSE) down

docker-logs:
	$(DOCKER_COMPOSE) logs -f

docker-restart:
	$(DOCKER_COMPOSE) restart app

docker-ps:
	$(DOCKER_COMPOSE) ps

pi-setup:
	bash scripts/setup.sh

pi-deploy:
	bash scripts/pi_deploy.sh

# ── TLS Certificate (self-signed) ──────────────────────────────────────────────
gen-certs:
	@mkdir -p config/nginx/certs
	openssl req -x509 -nodes -days 3650 -newkey rsa:4096 \
	  -keyout config/nginx/certs/selfsigned.key \
	  -out config/nginx/certs/selfsigned.crt \
	  -subj "/C=PT/ST=Lisbon/L=Lisbon/O=Investment Assistant/CN=investment-assistant"
	@echo "Certs written to config/nginx/certs/"
	@echo "Mount them in docker-compose.yml under the nginx service."

.DEFAULT_GOAL := help
