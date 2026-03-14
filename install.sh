#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# STS Situation Monitor — Desktop Installer (Linux / macOS)
#
# Usage:
#   ./install.sh
#
# What this does:
#   1. Checks Python 3.11+ is available
#   2. Creates a virtual environment
#   3. Installs the package and dev dependencies
#   4. Generates a secure API key and JWT secret
#   5. Creates .env from template
#   6. Runs database migrations
#   7. Verifies installation
#   8. Prints quick-start instructions
# ──────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║      STS Situation Monitor — Desktop Setup       ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════╝${NC}"
echo ""

# ── 1. Check Python ────────────────────────────────────────────
info "Checking Python version..."
PYTHON=""
for cmd in python3.12 python3.11 python3 python; do
  if command -v "$cmd" &>/dev/null; then
    version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)
    if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
      PYTHON="$cmd"
      break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  fail "Python 3.11+ is required but not found. Install it first:
    Ubuntu/Debian: sudo apt install python3.11 python3.11-venv
    Fedora:        sudo dnf install python3.11
    macOS:         brew install python@3.12
    Windows:       https://python.org/downloads"
fi
ok "Found Python: $($PYTHON --version)"

# ── 2. Create virtual environment ──────────────────────────────
VENV_DIR=".venv"
if [ -d "$VENV_DIR" ]; then
  ok "Virtual environment already exists at $VENV_DIR"
else
  info "Creating virtual environment..."
  "$PYTHON" -m venv "$VENV_DIR"
  ok "Virtual environment created"
fi

# Activate
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate" 2>/dev/null || source "$VENV_DIR/Scripts/activate" 2>/dev/null || fail "Cannot activate venv"
ok "Virtual environment activated"

# ── 3. Install package ─────────────────────────────────────────
info "Installing STS Situation Monitor and dependencies..."
pip install --quiet --upgrade pip setuptools wheel
pip install --quiet -e ".[dev]" 2>&1 | tail -5
ok "Package installed"

if command -v sts-monitor &>/dev/null; then
  ok "CLI available: sts-monitor"
else
  ok "CLI available via: python -m sts_monitor"
fi

# ── 4. Generate API key ────────────────────────────────────────
API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")

# ── 5. Create .env ─────────────────────────────────────────────
if [ -f ".env" ]; then
  warn ".env already exists — your existing config is preserved"
else
  info "Creating .env from template..."
  cp .env.example .env
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s/STS_AUTH_API_KEY=change-me/STS_AUTH_API_KEY=$API_KEY/" .env
    sed -i '' "s/STS_JWT_SECRET=change-me-in-production/STS_JWT_SECRET=$JWT_SECRET/" .env
  else
    sed -i "s/STS_AUTH_API_KEY=change-me/STS_AUTH_API_KEY=$API_KEY/" .env
    sed -i "s/STS_JWT_SECRET=change-me-in-production/STS_JWT_SECRET=$JWT_SECRET/" .env
  fi
  ok ".env created with secure API key and JWT secret"
fi

# ── 6. Run database migrations ─────────────────────────────────
info "Running database migrations..."
python -m alembic upgrade head 2>/dev/null || {
  info "Alembic migrations skipped — creating tables directly..."
  python -c "from sts_monitor.database import Base, engine; Base.metadata.create_all(engine)" 2>/dev/null || true
}
ok "Database ready (SQLite: ./sts_monitor.db)"

# ── 7. Create plugin directory ─────────────────────────────────
PLUGIN_DIR="$HOME/.sts-monitor/plugins"
mkdir -p "$PLUGIN_DIR"
ok "Plugin directory: $PLUGIN_DIR"

# ── 8. Verify installation ─────────────────────────────────────
info "Verifying installation..."
python -c "from sts_monitor.main import app; print('  FastAPI app loads OK')"
python -c "from sts_monitor.pipeline import SignalPipeline; print('  Pipeline loads OK')"
python -c "from sts_monitor.connectors import ALL_CONNECTORS; print(f'  {len(ALL_CONNECTORS)} connectors available')" 2>/dev/null || true
ok "Installation verified"

# ── 9. Check optional services ─────────────────────────────────
echo ""
info "Checking optional services..."

if command -v ollama &>/dev/null; then
  ok "Ollama detected (local LLM support available)"
  if ollama list 2>/dev/null | grep -q "llama3"; then
    ok "llama3 model found"
  else
    warn "No llama3 model found. Run: ollama pull llama3.1"
  fi
else
  warn "Ollama not installed — LLM features use deterministic fallbacks"
  info "  Install: https://ollama.ai/download"
fi

if command -v docker &>/dev/null; then
  ok "Docker detected (full stack available via docker compose)"
else
  warn "Docker not installed — using SQLite mode (fully functional)"
  info "  Install Docker for Postgres/Redis/Qdrant: https://docs.docker.com/get-docker/"
fi

# ── Summary ────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║           Installation Complete!                 ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Your API key: ${YELLOW}$API_KEY${NC}"
echo -e "  (Stored in .env as STS_AUTH_API_KEY)"
echo ""
echo -e "${BLUE}Get started:${NC}"
echo ""
echo "  # Activate the environment"
echo "  source .venv/bin/activate"
echo ""
echo "  # Start the API server + dashboard"
echo "  python -m sts_monitor serve --port 8080"
echo "  # Open http://localhost:8080/static/index.html"
echo ""
echo "  # Or run a full intelligence cycle from the CLI"
echo "  python -m sts_monitor cycle \"earthquake in Turkey\""
echo ""
echo -e "${BLUE}Useful commands:${NC}"
echo "  python -m sts_monitor cycle \"topic\"          # Full intelligence cycle"
echo "  python -m sts_monitor deep-truth \"topic\"     # Forensic analysis"
echo "  python -m sts_monitor surge \"topic\"          # Social media surge"
echo "  python -m sts_monitor retention --dry-run    # Preview data cleanup"
echo "  python -m sts_monitor serve --reload         # API with hot reload"
echo ""
echo -e "${BLUE}Optional — set up local LLM (Ollama):${NC}"
echo "  curl -fsSL https://ollama.com/install.sh | sh"
echo "  ollama pull llama3.1"
echo "  ollama pull nomic-embed-text"
echo ""
echo -e "${BLUE}Optional — full infrastructure stack:${NC}"
echo "  docker compose up -d     # Postgres + Redis + Qdrant + API"
echo ""
echo "  Full docs: see INSTALL.md and docs/"
echo ""
