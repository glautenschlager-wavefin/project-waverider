#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Waverider Setup Wizard
#
# One-command setup for Wave engineers:
#   make setup
#
# What it does:
#   1. Installs & starts Ollama, pulls the embedding model
#   2. Starts Docker services (Neo4j)
#   3. Discovers locally cloned Wave repos
#   4. Lets you pick which repos to index
#   5. Indexes selected repos
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_DIR="$HOME/.waverider"
CONFIG_FILE="$CONFIG_DIR/config.env"
GH_ORG="waveaccounting"
DEFAULT_REPOS_DIR="$HOME/wave/src"
EMBEDDING_MODEL="nomic-embed-text"
EXCLUDE_PATTERNS="node_modules .git __pycache__ .venv venv dist build .tox migrations static fixtures vendor .mypy_cache .pytest_cache htmlcov .coverage .next .turbo .cache site-packages include/python lib/python bin/python sitestatic"

# ── Colours ───────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()  { echo -e "${BLUE}▸${NC} $*"; }
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC} $*"; }
fail()  { echo -e "${RED}✗${NC} $*"; exit 1; }
header() { echo -e "\n${BOLD}$*${NC}"; echo "$(printf '─%.0s' $(seq 1 60))"; }

# ──────────────────────────────────────────────────────────────────────
# Step 1: Ollama
# ──────────────────────────────────────────────────────────────────────
setup_ollama() {
    header "Step 1/4 · Ollama (embedding engine)"

    if command -v ollama &>/dev/null; then
        ok "Ollama already installed"
    else
        info "Installing Ollama via Homebrew…"
        brew install ollama
        ok "Ollama installed"
    fi

    # Ensure Ollama is running
    if ! curl -sf http://localhost:11434/api/version &>/dev/null; then
        info "Starting Ollama…"
        brew services start ollama
        # Wait for it
        for i in $(seq 1 15); do
            curl -sf http://localhost:11434/api/version &>/dev/null && break
            sleep 2
        done
        curl -sf http://localhost:11434/api/version &>/dev/null \
            || fail "Ollama failed to start. Check: brew services list"
    fi
    ok "Ollama is running"

    # Pull embedding model
    if ollama list 2>/dev/null | grep -q "$EMBEDDING_MODEL"; then
        ok "Model '$EMBEDDING_MODEL' already pulled"
    else
        info "Pulling model '$EMBEDDING_MODEL' (this may take a minute)…"
        ollama pull "$EMBEDDING_MODEL"
        ok "Model '$EMBEDDING_MODEL' ready"
    fi
}

# ──────────────────────────────────────────────────────────────────────
# Step 2: Docker services
# ──────────────────────────────────────────────────────────────────────
setup_docker() {
    header "Step 2/4 · Docker services (Neo4j + Waverider image)"

    if ! docker info &>/dev/null; then
        fail "Docker daemon is not running. Start Rancher Desktop / Docker Desktop first."
    fi

    cd "$PROJECT_DIR"

    # Set a default Neo4j password if not configured
    if [[ -z "${NEO4J_PASSWORD:-}" ]]; then
        if [[ -f .env ]] && grep -q '^NEO4J_PASSWORD=' .env; then
            export "$(grep '^NEO4J_PASSWORD=' .env)"
        else
            export NEO4J_PASSWORD="waverider"
            info "Using default NEO4J_PASSWORD (change in .env for production)"
        fi
    fi

    # Build the waverider image (needed for indexing inside the container)
    info "Building Waverider Docker image…"
    docker compose build waverider 2>&1 | tail -3
    ok "Waverider image built"

    local neo4j_status
    neo4j_status=$(docker compose ps --format '{{.Name}} {{.Status}}' 2>/dev/null | grep waverider-neo4j || true)
    if echo "$neo4j_status" | grep -qi "up"; then
        ok "Neo4j is already running"
    else
        info "Starting Neo4j…"
        docker compose up -d neo4j
        info "Waiting for Neo4j healthcheck…"
        for i in $(seq 1 30); do
            if docker compose ps --format '{{.Status}}' neo4j 2>/dev/null | grep -qi "healthy"; then
                break
            fi
            sleep 3
        done
        docker compose ps --format '{{.Status}}' neo4j 2>/dev/null | grep -qi "healthy" \
            || warn "Neo4j may still be starting up — indexing will proceed without it"
    fi
    ok "Docker services ready"
}

# ──────────────────────────────────────────────────────────────────────
# Step 3: Discover repos
# ──────────────────────────────────────────────────────────────────────
discover_repos() {
    header "Step 3/4 · Discover codebases"

    # Ask for repos directory
    local repos_dir="$DEFAULT_REPOS_DIR"
    if [[ -f "$CONFIG_FILE" ]] && grep -q '^REPOS_DIR=' "$CONFIG_FILE"; then
        repos_dir="$(grep '^REPOS_DIR=' "$CONFIG_FILE" | cut -d= -f2-)"
        info "Using saved repos directory: $repos_dir"
    fi

    echo -e "\nWhere are your Wave repos cloned?"
    read -r -p "  Repos directory [$repos_dir]: " user_dir
    repos_dir="${user_dir:-$repos_dir}"
    repos_dir="${repos_dir/#\~/$HOME}"  # expand tilde

    [[ -d "$repos_dir" ]] || fail "Directory not found: $repos_dir"

    # List local directories
    local local_repos=()
    while IFS= read -r dir; do
        local name
        name="$(basename "$dir")"
        # Must contain at least some source files (not empty/meta repos)
        if [[ -d "$dir/.git" ]] || [[ -d "$dir/src" ]] || ls "$dir"/*.py "$dir"/*.js "$dir"/*.ts "$dir"/*.rb 2>/dev/null | head -1 &>/dev/null; then
            local_repos+=("$name")
        fi
    done < <(find "$repos_dir" -mindepth 1 -maxdepth 1 -type d | sort)

    if [[ ${#local_repos[@]} -eq 0 ]]; then
        fail "No repos found in $repos_dir"
    fi

    ok "Found ${#local_repos[@]} repos in $repos_dir"
    echo ""

    # Show numbered list for selection
    echo -e "${BOLD}Available repos:${NC}"
    echo ""
    local i=1
    for repo in "${local_repos[@]}"; do
        printf "  %3d) %s\n" "$i" "$repo"
        ((i++))
    done

    echo ""
    echo "Which repos do you want to index?"
    echo "  Enter numbers separated by spaces, ranges (1-5), or 'all'"
    echo "  Example: 1 3 7-10"
    echo ""
    read -r -p "  Selection [all]: " selection
    selection="${selection:-all}"

    # Parse selection
    SELECTED_REPOS=()
    if [[ "$selection" == "all" ]]; then
        SELECTED_REPOS=("${local_repos[@]}")
    else
        for token in $selection; do
            if [[ "$token" =~ ^([0-9]+)-([0-9]+)$ ]]; then
                local start="${BASH_REMATCH[1]}"
                local end="${BASH_REMATCH[2]}"
                for ((j=start; j<=end; j++)); do
                    if [[ $j -ge 1 && $j -le ${#local_repos[@]} ]]; then
                        SELECTED_REPOS+=("${local_repos[$((j-1))]}")
                    fi
                done
            elif [[ "$token" =~ ^[0-9]+$ ]]; then
                if [[ $token -ge 1 && $token -le ${#local_repos[@]} ]]; then
                    SELECTED_REPOS+=("${local_repos[$((token-1))]}")
                fi
            else
                warn "Ignoring invalid input: $token"
            fi
        done
    fi

    if [[ ${#SELECTED_REPOS[@]} -eq 0 ]]; then
        fail "No repos selected"
    fi

    echo ""
    ok "Selected ${#SELECTED_REPOS[@]} repo(s): ${SELECTED_REPOS[*]}"

    # Save config
    mkdir -p "$CONFIG_DIR"
    cat > "$CONFIG_FILE" <<EOF
# Waverider configuration — generated by setup wizard
REPOS_DIR=$repos_dir
SELECTED_REPOS=${SELECTED_REPOS[*]}
EMBEDDING_MODEL=$EMBEDDING_MODEL
GH_ORG=$GH_ORG
EOF
    ok "Config saved to $CONFIG_FILE"

    # Export for next step
    REPOS_DIR="$repos_dir"
}

# ──────────────────────────────────────────────────────────────────────
# Step 4: Index selected repos
# ──────────────────────────────────────────────────────────────────────
index_repos() {
    header "Step 4/4 · Index codebases (inside Docker)"

    local total=${#SELECTED_REPOS[@]}
    local current=0
    local failed=()

    for repo in "${SELECTED_REPOS[@]}"; do
        ((++current))
        local repo_path="$REPOS_DIR/$repo"

        echo ""
        info "[$current/$total] Indexing: $repo"

        if [[ ! -d "$repo_path" ]]; then
            warn "Skipping $repo — directory not found"
            failed+=("$repo")
            continue
        fi

        cd "$PROJECT_DIR"
        # Run build_index.py inside the waverider container.
        # Mount the repo read-only at /src/<repo> so the container can read it.
        # The waverider-data and waverider-indices volumes persist the output.
        if docker compose run --rm \
            --no-deps \
            -v "$repo_path:/src/$repo:ro" \
            -e OLLAMA_HOST="${OLLAMA_HOST:-http://host.docker.internal:11434}" \
            --entrypoint python \
            waverider \
            scripts/build_index.py \
                --codebase-path "/src/$repo" \
                --index-name "$repo" \
                --description "Wave $repo service" \
                --exclude $EXCLUDE_PATTERNS \
                --embedding-provider ollama \
                --model "$EMBEDDING_MODEL" 2>&1 | tail -8; then
            ok "$repo indexed"
        else
            warn "$repo indexing failed (continuing…)"
            failed+=("$repo")
        fi
    done

    echo ""
    header "Setup Complete"
    ok "${#SELECTED_REPOS[@]} repo(s) processed"

    if [[ ${#failed[@]} -gt 0 ]]; then
        warn "Failed: ${failed[*]}"
        echo "  Re-run individual repos with:"
        echo "    poetry run python scripts/build_index.py --codebase-path <path> --index-name <name>"
    fi

    echo ""
    echo -e "${BOLD}Next steps:${NC}"
    echo "  • Start the MCP server:  make docker-up"
    echo "  • Re-index a single repo:"
    echo "      make index-repo REPO=<name>  (indexes ~/wave/src/<name>)"
    echo "  • Re-run this wizard:    make setup"
    echo ""
}

# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────
main() {
    echo ""
    echo -e "${BOLD}🏄 Waverider Setup Wizard${NC}"
    echo "$(printf '═%.0s' $(seq 1 60))"
    echo ""

    setup_ollama
    setup_docker
    discover_repos
    index_repos
}

main "$@"
