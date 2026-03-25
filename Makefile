.PHONY: help install format lint lint-fix type-check test all-checks neo4j-start neo4j-stop neo4j-restart neo4j-status neo4j-console mcp-start shell index-ollama clean

help:
	@echo "Waverider Development Commands"
	@echo "==============================="
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
	@echo "make index           Build index with Ollama (the default) embeddings"
	@echo "make clean           Remove cache and temporary files"

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
	poetry run python scripts/build_index.py --codebase-path ./ --index-name waverider --embedding-provider ollama --model nomic-embed-text

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist

.DEFAULT_GOAL := help
