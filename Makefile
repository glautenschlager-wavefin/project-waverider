.PHONY: help setup uninstall install format lint lint-fix type-check test all-checks neo4j-start neo4j-stop neo4j-restart neo4j-status neo4j-console mcp-start shell index index-repo clean docker-build docker-up docker-down docker-logs

help:
	@echo "Waverider Development Commands"
	@echo "==============================="
	@echo "make setup           Run the setup wizard (install Ollama, start Neo4j, index repos)"
	@echo "make install         Install dependencies"
	@echo "make format          Format code (black + isort)"
	@echo "make lint            Run linters (ruff)"
	@echo "make lint-fix        Auto-fix lint issues with ruff"
	@echo "make type-check      Run type checker (mypy)"
	@echo "make test            Run tests (pytest)"
	@echo "make all-checks      Run all checks (format, lint, type-check, test)"
	@echo "make neo4j-start     Start Neo4j with Homebrew services"
	@echo "make neo4j-stop      Stop Neo4j Homebrew service"
	@echo "make neo4j-restart   Restart Neo4j Homebrew service"
	@echo "make neo4j-status    Show Neo4j Homebrew service status"
	@echo "make neo4j-console   Run Neo4j in the foreground"
	@echo "make mcp-start       Start the Waverider MCP server (stdio)"
	@echo "make shell           Start a Python shell (poetry env)"
	@echo "make index           Build index for waverider itself (in Docker)"
	@echo "make index-repo REPO=<name>  Index a Wave repo (from ~/wave/src/<name>)"
	@echo "make clean           Remove cache and temporary files"
	@echo ""
	@echo "Docker:"
	@echo "make docker-build    Build the Waverider Docker image"
	@echo "make docker-up       Start all services (Neo4j + MCP server)"
	@echo "make docker-down     Stop and remove all containers"
	@echo "make docker-logs     Tail logs for all services"
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

neo4j-start:
	brew services start neo4j

neo4j-stop:
	brew services stop neo4j

neo4j-restart:
	brew services restart neo4j

neo4j-status:
	brew services list | grep neo4j || true

neo4j-console:
	/opt/homebrew/opt/neo4j/bin/neo4j console

mcp-start:
	poetry run python -m waverider.mcp_server

shell:
	poetry run python

index:
	docker compose run --rm --no-deps \
		-v "$$(pwd):/src/waverider:ro" \
		-e OLLAMA_HOST=$${OLLAMA_HOST:-http://host.docker.internal:11434} \
		--entrypoint python waverider \
		scripts/build_index.py \
			--codebase-path /src/waverider \
			--index-name waverider \
			--embedding-provider ollama \
			--model nomic-embed-text

index-repo:
	@test -n "$(REPO)" || (echo "Usage: make index-repo REPO=<name>" && exit 1)
	docker compose run --rm --no-deps \
		-v "$${REPOS_DIR:-$$HOME/wave/src}/$(REPO):/src/$(REPO):ro" \
		-e OLLAMA_HOST=$${OLLAMA_HOST:-http://host.docker.internal:11434} \
		--entrypoint python waverider \
		scripts/build_index.py \
			--codebase-path "/src/$(REPO)" \
			--index-name "$(REPO)" \
			--description "Wave $(REPO) service" \
			--exclude node_modules .git __pycache__ .venv venv dist build .tox migrations static fixtures vendor \
			--embedding-provider ollama \
			--model nomic-embed-text

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

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f

.DEFAULT_GOAL := help
