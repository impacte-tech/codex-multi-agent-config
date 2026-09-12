#!/usr/bin/env bash
# Installs the codex-multi-agent-config files into ~/.codex/
#
# Usage:
#   ./install.sh              # max-savings policy (default)
#   ./install.sh --mode hybrid  # OpenAI sub-agents primary, local fallback
set -euo pipefail

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
MODE="max"
[[ "${1:-}" == "--mode" && "${2:-}" != "" ]] && MODE="$2"
case "$MODE" in
  max|hybrid) ;;
  *) echo "Unknown mode: $MODE (use 'max' or 'hybrid')" >&2; exit 1 ;;
esac

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing to $CODEX_HOME (mode: $MODE)"

# 1. Backup existing config
if [[ -f "$CODEX_HOME/config.toml" ]]; then
  cp "$CODEX_HOME/config.toml" "$CODEX_HOME/config.toml.bak-$(date +%Y%m%d-%H%M%S)"
  echo "    backed up config.toml"
fi

# 2. Copy the MCP bridge
mkdir -p "$CODEX_HOME/mcp"
cp "$REPO_DIR/mcp/ollama_bridge.py" "$CODEX_HOME/mcp/ollama_bridge.py"
echo "    installed mcp/ollama_bridge.py"

# 3. Copy agent roles (used by the hybrid mode; harmless otherwise)
mkdir -p "$CODEX_HOME/agents"
cp "$REPO_DIR/agents/"*.toml "$CODEX_HOME/agents/"
echo "    installed agents/{default,worker,explorer}.toml"

# 4. Install the delegation policy
cp "$REPO_DIR/AGENTS.md" "$CODEX_HOME/AGENTS.md"
if [[ "$MODE" == "hybrid" && -f "$REPO_DIR/AGENTS.hybrid.md" ]]; then
  cp "$REPO_DIR/AGENTS.hybrid.md" "$CODEX_HOME/AGENTS.md"
fi
echo "    installed AGENTS.md (mode: $MODE)"

# 5. Register the MCP server (idempotent)
if grep -q "mcp_servers.ollama_bridge" "$CODEX_HOME/config.toml" 2>/dev/null; then
  echo "    config.toml already registers ollama_bridge"
else
  if [[ ! -f "$CODEX_HOME/config.toml" ]]; then
    touch "$CODEX_HOME/config.toml"
  fi
  cat >> "$CODEX_HOME/config.toml" <<EOF

# --- Local Ollama IO workers (free delegation via MCP bridge) ---
[mcp_servers.ollama_bridge]
command = "/usr/bin/python3"
args = ["$CODEX_HOME/mcp/ollama_bridge.py"]
tool_timeout_sec = 1800
EOF
  echo "    appended [mcp_servers.ollama_bridge] to config.toml"
fi

# 6. Sanity checks
if curl -s --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo "    ollama server: running"
else
  echo "    WARNING: ollama server not reachable at localhost:11434" >&2
fi

cat <<'EOF'

Done. Next steps:
  1. Pull a worker model:   ollama pull qwen3.5:4b
     (optional, stronger):  ollama pull lfm2.5:8b
  2. Fully restart Codex (desktop app or CLI session) — config loads at startup.
  3. Ask for IO work normally, e.g.:
       "Explore how auth is handled in this repo and report the entry points"
     The main thread will delegate it to a local worker via delegate_ollama_agent.
  4. Watch it work:         ollama ps
EOF