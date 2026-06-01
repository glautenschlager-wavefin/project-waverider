.PHONY: help setup uninstall install format lint lint-fix type-check test all-checks mcp-start shell index index-repo index-all clean docker-build docker-up docker-down docker-logs docker-ps token-analysis db-shell db-status

help:
	@echo "Waverider Development Commands"
	@echo "==============================="
	@echo "make setup           Run the setup wizard (install Ollama, start services, index repos)"
	@echo "make install         Install dependencies"
	@echo "make format          Format code (black + isort)"
	@echo "make lint            Run linters (ruff)"
	@echo "make lint-fix        Auto-fix lint issues with ruff"
	@echo "make type-check      Run type checker (mypy)"
	@echo "make test            Run tests (pytest)"
	@echo "make all-checks      Run all checks (format, lint, type-check, test)"
	@echo "make mcp-start       Start the Waverider MCP server (stdio)"
	@echo "make shell           Start a Python shell (poetry env)"
	@echo "make index           Index waverider itself (CocoIndex incremental)"
	@echo "make index-repo REPO=<name>  Index a Wave repo (incremental)"
	@echo "make index-all       Index all Wave repos (incremental)"
	@echo "make clean           Remove cache and temporary files"
	@echo ""
	@echo "Database:"
	@echo "make db-shell        Open psql shell to ParadeDB"
	@echo "make db-status       Show database stats (tables, row counts)"
	@echo ""
	@echo "Analysis:"
	@echo "make token-analysis  Run the token savings analysis (in Docker)"
	@echo ""
	@echo "Docker:"
	@echo "make docker-build    Build the Waverider Docker image"
	@echo "make docker-up       Start all services (ParadeDB + Neo4j + MCP server)"
	@echo "make docker-down     Stop and remove all containers"
	@echo "make docker-logs     Tail logs for all services"
	@echo "make docker-ps       Show running containers"
	@echo ""
	@echo "Uninstall:"
	@echo "make uninstall       Remove MCP registration, instructions, containers & volumes"

setup:
	@bash scripts/setup.sh

install:
	poetry install

format:
	poetry run black src/ scripts/ tests/
	poetry run isort src/ scripts/ tests/

lint:
	poetry run ruff check src/ scripts/ tests/

lint-fix:
	poetry run ruff check --fix src/ scripts/ tests/

type-check:
	poetry run mypy src/

test:
	poetry run pytest tests/ -v

all-checks: format lint type-check test

mcp-start:
	poetry run python -m waverider.mcp_server

shell:
	poetry run python

index:
	docker compose run --rm \
		-v "$$(pwd):/src/waverider:ro" \
		-e OLLAMA_HOST=$${OLLAMA_HOST:-http://host.docker.internal:11434} \
		--entrypoint python waverider \
		scripts/build_index.py \
			--codebase-path /src/waverider \
			--index-name waverider

index-repo:
	@test -n "$(REPO)" || (echo "Usage: make index-repo REPO=<name>" && exit 1)
	docker compose run --rm \
		-v "$${REPOS_DIR:-$$HOME/wave/src}/$(REPO):/src/$(REPO):ro" \
		-e OLLAMA_HOST=$${OLLAMA_HOST:-http://host.docker.internal:11434} \
		--entrypoint python waverider \
		scripts/build_index.py \
			--codebase-path "/src/$(REPO)" \
			--index-name "$(REPO)" \
			--description "Wave $(REPO) service"

index-all:
	@echo "Indexing all Wave repos (incremental)..."
	@for repo in identity reef payroll embedded-payroll next-wave central-risk next-accounting accounting; do \
		echo ""; echo ">>> $$repo"; \
		$(MAKE) index-repo REPO=$$repo || true; \
	done

db-shell:
	docker compose exec paradedb psql -U waverider -d waverider

db-status:
	@docker compose exec paradedb psql -U waverider -d waverider -c \
		"SELECT tablename, pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size FROM pg_tables WHERE schemaname='public' ORDER BY tablename;"
	@docker compose exec paradedb psql -U waverider -d waverider -c \
		"SELECT relname as table, n_live_tup as rows FROM pg_stat_user_tables ORDER BY n_live_tup DESC;"

docker-ps:
	docker compose ps

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist

uninstall:
	@bash scripts/uninstall.sh

token-analysis:
	docker compose run --rm --no-deps \
		--entrypoint python waverider \
		scripts/token_analysis.py \
			--engineers $${ENGINEERS:-100} \
			--queries-per-day $${QUERIES_PER_DAY:-8} \
			--working-days $${WORKING_DAYS:-250}

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

.DEFAULT_GOAL := help
