# ═══════════════════════════════════════════════════════════════
#  Makefile — Developer Commands
#  Payment Reliability Agent System
# ═══════════════════════════════════════════════════════════════

.PHONY: help install dev-setup infra-up infra-down test lint run demo api clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─── Setup ────────────────────────────────────────────────────

install: ## Install Python dependencies
	poetry install

dev-setup: install ## Full dev setup: install deps, copy env, start infra
	@if not exist .env copy .env.example .env
	@echo "✅ Dev setup complete. Edit .env with your API keys."

# ─── Infrastructure ──────────────────────────────────────────

infra-up: ## Start Docker Compose infrastructure
	docker compose up -d
	@echo "✅ Infrastructure started. Services:"
	@echo "   Kafka:      localhost:9092"
	@echo "   Redis:      localhost:6379"
	@echo "   PostgreSQL: localhost:5432"
	@echo "   N8n:        http://localhost:5678"
	@echo "   Prometheus: http://localhost:9090"
	@echo "   Grafana:    http://localhost:3000 (admin/agent_admin_2024)"
	@echo "   Loki:       http://localhost:3100"
	@echo "   Tempo:      http://localhost:3200"

infra-down: ## Stop Docker Compose infrastructure
	docker compose down

infra-reset: ## Stop and remove all volumes (fresh start)
	docker compose down -v

infra-logs: ## Tail infrastructure logs
	docker compose logs -f

# ─── Running ─────────────────────────────────────────────────

demo: ## Run demo mode (5 events, 2 anomalies)
	poetry run python main.py --mode demo

run: ## Run streaming mode for 60 seconds
	poetry run python main.py --mode stream --duration 60 --eps 5 --anomaly-prob 0.1

batch: ## Run batch mode (50 events)
	poetry run python main.py --mode batch

api: ## Start the FastAPI server
	poetry run uvicorn api:app --host 0.0.0.0 --port 8000 --reload

# ─── Testing ─────────────────────────────────────────────────

test: ## Run all tests
	poetry run pytest tests/ -v

test-unit: ## Run unit tests only
	poetry run pytest tests/unit/ -v

test-cov: ## Run tests with coverage
	poetry run pytest tests/ --cov=agents --cov=shared --cov=data_pipeline --cov-report=html

# ─── Code Quality ────────────────────────────────────────────

lint: ## Run linting (ruff)
	poetry run ruff check .

lint-fix: ## Auto-fix lint issues
	poetry run ruff check --fix .

format: ## Format code
	poetry run ruff format .

typecheck: ## Run type checking (mypy)
	poetry run mypy agents/ shared/ --ignore-missing-imports

# ─── Knowledge Base ──────────────────────────────────────────

ingest-runbooks: ## Ingest sample runbooks into knowledge base
	poetry run python -c "import asyncio; from knowledge_base.ingestion.pipeline import RunbookIngestionPipeline; asyncio.run(RunbookIngestionPipeline().ingest_sample_runbooks())"

# ─── Cleanup ─────────────────────────────────────────────────

clean: ## Clean build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf htmlcov/ .coverage 2>/dev/null || true
