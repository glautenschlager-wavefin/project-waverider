#!/usr/bin/env bash
# Index multiple Wave codebases
set -e

WAVERIDER_DIR="/Users/glautenschlager/dev/project waverider"
WAVE_SRC="/Users/glautenschlager/wave/src"
EXCLUDE="node_modules .git __pycache__ .venv venv dist build .tox migrations static fixtures vendor .mypy_cache .pytest_cache htmlcov .coverage .next .turbo .cache site-packages include/python lib/python bin/python sitestatic"

cd "$WAVERIDER_DIR"

index_repo() {
  local name="$1"
  local desc="$2"
  local path="$WAVE_SRC/$name"

  if [ ! -d "$path" ]; then
    echo "SKIP: $path not found"
    return
  fi

  echo ""
  echo "################################################################"
  echo "# Indexing (SQLite): $name"
  echo "################################################################"
  poetry run python scripts/build_index.py \
    --codebase-path "$path" \
    --index-name "$name" \
    --description "$desc" \
    --exclude $EXCLUDE \
    --full
  echo "Done (SQLite): $name"

  echo ""
  echo "# Indexing (Neo4j): $name"
  poetry run python scripts/index_neo4j.py \
    --codebase-path "$path" \
    --index-name "$name" \
    --description "$desc" \
    --clear
  echo "Done (Neo4j): $name"
}

index_repo "identity" "Wave identity Python service"
index_repo "reef" "Wave reef TypeScript service"
index_repo "payroll" "Wave payroll Ruby service"
index_repo "next-wave" "Wave main web application (React/TypeScript)"
index_repo "central-risk" "Wave central risk service"
index_repo "next-accounting" "Wave next-accounting service"
index_repo "accounting" "Wave accounting service"
