.PHONY: help install format lint type-check test clean

help:
	@echo "Waverider Development Commands"
	@echo "==============================="
	@echo "make install         Install dependencies"
	@echo "make format          Format code (black + isort)"
	@echo "make lint            Run linters (ruff)"
	@echo "make type-check      Run type checker (mypy)"
	@echo "make test            Run tests (pytest)"
	@echo "make all-checks      Run all checks (format, lint, type-check, test)"
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

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist

.DEFAULT_GOAL := help
