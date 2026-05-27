#!/usr/bin/env bash
# DEPRECATED: This script is superseded by the DB-backed codebase registry.
#
# To manage codebases, use the MCP admin tools:
#   register_codebase, list_codebases, set_codebase_enabled, deregister_codebase
#
# To seed the registry from this list (one-time migration):
#   poetry run python scripts/seed_registry.py
#
# To trigger reindexing:
#   poetry run python scripts/reindex_if_changed.py --once
#
# This file is kept for historical reference and manual emergency use only.
# -------------------------------------------------------------------------
#
# (Original description: Index multiple Wave codebases — Phase 4, CocoIndex incremental by default)
#
# Usage:
#   ./scripts/index_wave_repos.sh              # incremental (default)
#   ./scripts/index_wave_repos.sh --legacy     # use legacy manual indexer
#   ./scripts/index_wave_repos.sh --use-neo4j  # also build Neo4j graph
set -e

WAVERIDER_DIR="/Users/glautenschlager/dev/project waverider"
WAVE_SRC="/Users/glautenschlager/wave/src"

# Pass-through flags (e.g. --legacy, --use-neo4j, --full)
EXTRA_FLAGS=("$@")

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
  echo "# Indexing: $name"
  echo "################################################################"
  poetry run python scripts/build_index.py \
    --codebase-path "$path" \
    --index-name "$name" \
    --description "$desc" \
    "${EXTRA_FLAGS[@]}"
  echo "Done: $name"
}

index_repo "identity" "Wave identity Python service"
index_repo "reef" "Wave reef TypeScript service"
index_repo "payroll" "Wave payroll Ruby service"
index_repo "next-wave" "Wave main web application (React/TypeScript)"
index_repo "central-risk" "Wave central risk service"
index_repo "next-accounting" "Wave next-accounting service"
index_repo "accounting" "Wave accounting service"
