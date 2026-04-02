#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# Waverider Uninstall
#
# Cleanly removes all Waverider artifacts from the local machine:
#   - MCP server registration from VS Code
#   - Copilot instructions file
#   - Docker containers and volumes
#   - Local config (~/.waverider)
#
# Usage: make uninstall
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
skip() { echo -e "  · $*"; }

echo ""
echo -e "${BOLD}Uninstalling Waverider${NC}"
echo "──────────────────────────────────────────────────────────"

# ── Detect VS Code config dir ────────────────────────────────────────
vscode_dir="$HOME/Library/Application Support/Code/User"
if [[ ! -d "$vscode_dir" ]]; then
    vscode_dir="$HOME/.config/Code/User"
fi

# ── 1. Remove MCP server entry ──────────────────────────────────────
mcp_config="$vscode_dir/mcp.json"
if [[ -f "$mcp_config" ]]; then
    if python3 -c "
import json, sys
path = sys.argv[1]
with open(path) as f:
    d = json.load(f)
if 'waverider' in d.get('servers', {}):
    del d['servers']['waverider']
    with open(path, 'w') as f:
        json.dump(d, f, indent=2)
        f.write('\n')
    sys.exit(0)
sys.exit(1)
" "$mcp_config" 2>/dev/null; then
        ok "Removed waverider MCP server from $mcp_config"
    else
        skip "No waverider entry in $mcp_config"
    fi
else
    skip "No VS Code MCP config found"
fi

# ── 2. Remove instructions file ─────────────────────────────────────
instructions="$vscode_dir/prompts/waverider.instructions.md"
if [[ -f "$instructions" ]]; then
    rm "$instructions"
    ok "Removed $instructions"
else
    skip "No instructions file found"
fi

# ── 3. Stop containers and remove volumes ────────────────────────────
cd "$PROJECT_DIR"
if docker compose ps -q 2>/dev/null | grep -q .; then
    docker compose down -v 2>/dev/null
    ok "Containers stopped, volumes removed"
elif docker volume ls -q 2>/dev/null | grep -q projectwaverider; then
    docker compose down -v 2>/dev/null
    ok "Volumes removed"
else
    skip "No running containers or volumes"
fi

# ── 4. Remove local config ──────────────────────────────────────────
if [[ -f "$HOME/.waverider/config.env" ]]; then
    rm "$HOME/.waverider/config.env"
    rmdir "$HOME/.waverider" 2>/dev/null || true
    ok "Removed ~/.waverider/config.env"
else
    skip "No local config found"
fi

# ── 5. Stop Ollama and remove the embedding model ───────────────────
if command -v ollama &>/dev/null; then
    if ollama list 2>/dev/null | grep -q nomic-embed-text; then
        ollama rm nomic-embed-text 2>/dev/null
        ok "Removed nomic-embed-text model"
    else
        skip "nomic-embed-text model not installed"
    fi
    # Stop Ollama if Waverider was the only user — ask first
    echo ""
    read -r -p "  Stop Ollama service? Other tools may use it. [y/N]: " stop_ollama
    if [[ "${stop_ollama:-n}" =~ ^[Yy] ]]; then
        brew services stop ollama 2>/dev/null || true
        ok "Ollama service stopped"
    else
        skip "Ollama left running"
    fi
else
    skip "Ollama not installed"
fi

echo ""
echo -e "${BOLD}Done.${NC} Reload VS Code to complete removal."
echo ""
