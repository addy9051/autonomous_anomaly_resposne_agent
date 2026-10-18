# ═══════════════════════════════════════════════════════════════
#  Makefile — Developer Commands
#  Payment Reliability Agent System
# ═══════════════════════════════════════════════════════════════

.PHONY: help install dev-setup infra-up infra-down test lint demo demo-warning api modal-deploy modal-stop judge-sanity judge-gate simulate-labels judge-gate-simulated clean

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
	@echo "   PostgreSQL: localhost:5432"
	@echo "   Prometheus: http://localhost:9090"
	@echo "   Langfuse:   http://localhost:3001"
	@echo "   LLM server: run vLLM/Ollama yourself and set LLM_BASE_URL"

infra-down: ## Stop Docker Compose infrastructure
	docker compose down

infra-reset: ## Stop and remove all volumes (fresh start)
	docker compose down -v

infra-logs: ## Tail infrastructure logs
	docker compose logs -f

# ─── Running ─────────────────────────────────────────────────

demo: ## Fire a sample critical alert at the running API (needs `make api` in another shell)
	poetry run python scripts/send_sample_alert.py --severity critical

demo-warning: ## Fire a sample non-critical alert at the running API
	poetry run python scripts/send_sample_alert.py --severity warning

api: ## Start the FastAPI server (port 8010 to avoid clashing with other local services)
	poetry run uvicorn api:app --host 127.0.0.1 --port 8010 --reload

modal-deploy: ## Deploy the vLLM endpoints (chat, judge, embeddings, offline labeller) to Modal
	modal deploy infra/modal/vllm_app.py

modal-stop: ## Stop the Modal vLLM app (stops GPU spend)
	modal app stop sre-agent-vllm

judge-sanity: ## Run the LLM judge over the golden cases and check it follows the rubric
	poetry run python -m evals.judge_sanity

judge-gate: ## Judge-vs-human agreement gate (needs human labels)
	poetry run python -m evals.run_judge_gate

simulate-labels: ## AI-simulated labels (NOT human) from the Modal labeller; pass BASE_URL=https://...labeller.modal.run/v1
	poetry run python -m evals.simulate_labels --base-url $(BASE_URL)

judge-gate-simulated: ## Judge vs AI-simulated labels (model-vs-model agreement, not a human gate)
	poetry run python -m evals.run_judge_gate --labels evals/data/simulated_labels.jsonl --out evals/reports/judge_gate_simulated.json

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
