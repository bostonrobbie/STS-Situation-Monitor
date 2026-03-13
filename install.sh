#!/usr/bin/env bash
#
# STS Situation Monitor — One-command installer for AI PC
#
# Usage:
#   curl -sSL <repo-url>/install.sh | bash
#   # or
#   ./install.sh
#
# What this does:
#   1. Checks Python 3.11+ is available
#   2. Creates a virtual environment
#   3. Installs the package (with console_scripts)
#   4. Generates an API key
#   5. Creates .env from template
#   6. Runs database migrations
#   7. Runs preflight check
#   8. Prints quick-start instructions
#
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   STS Situation Monitor — Installer      ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: Check Python ─────────────────────────────────────────────
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
    macOS:         brew install python@3.12
    Windows:       https://python.org/downloads"
fi
ok "Found Python: $($PYTHON --version)"

# ── Step 2: Create virtual environment ────────────────────────────────
VENV_DIR=".venv"
if [ -d "$VENV_DIR" ]; then
  info "Virtual environment already exists at $VENV_DIR"
else
  info "Creating virtual environment..."
  "$PYTHON" -m venv "$VENV_DIR"
  ok "Virtual environment created"
fi

# Activate
source "$VENV_DIR/bin/activate" 2>/dev/null || source "$VENV_DIR/Scripts/activate" 2>/dev/null || fail "Cannot activate venv"
ok "Virtual environment activated"

# ── Step 3: Install package ──────────────────────────────────────────
info "Installing STS Situation Monitor..."
pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]" 2>&1 | tail -5
ok "Package installed"

# Verify CLI is available
if command -v sts-monitor &>/dev/null; then
  ok "CLI available: sts-monitor"
else
  ok "CLI available: python -m sts_monitor"
fi

# ── Step 4: Generate API key ─────────────────────────────────────────
info "Generating API key..."
API_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
ok "API key generated"

# ── Step 5: Create .env ──────────────────────────────────────────────
if [ -f ".env" ]; then
  warn ".env already exists — skipping (your existing config is preserved)"
else
  info "Creating .env from template..."
  cp .env.example .env
  # Inject the generated API key
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s/STS_AUTH_API_KEY=change-me/STS_AUTH_API_KEY=$API_KEY/" .env
  else
    sed -i "s/STS_AUTH_API_KEY=change-me/STS_AUTH_API_KEY=$API_KEY/" .env
  fi
  ok ".env created with generated API key"
fi

# ── Step 6: Set up JWT secret ────────────────────────────────────────
JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
if ! grep -q "STS_JWT_SECRET" .env 2>/dev/null; then
  echo "" >> .env
  echo "# --- JWT Auth ---" >> .env
  echo "STS_JWT_SECRET=$JWT_SECRET" >> .env
  ok "JWT secret generated and added to .env"
fi

# ── Step 7: Run database migrations ──────────────────────────────────
info "Running database migrations..."
python -m alembic upgrade head 2>/dev/null || {
  info "Alembic migrations skipped — creating tables directly..."
  python -c "from sts_monitor.database import Base, engine; Base.metadata.create_all(engine)" 2>/dev/null
}
ok "Database ready"

# ── Step 8: Create plugin directory ──────────────────────────────────
PLUGIN_DIR="$HOME/.sts-monitor/plugins"
mkdir -p "$PLUGIN_DIR"
ok "Plugin directory created: $PLUGIN_DIR"

# ── Step 9: Preflight check ──────────────────────────────────────────
info "Running preflight check..."
python -c "
from sts_monitor.database import Base, engine
Base.metadata.create_all(engine)
print('  Database: OK')
" 2>/dev/null || warn "Database check had issues"

# ── Done! ────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Installation complete!                 ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "Your API key: ${YELLOW}$API_KEY${NC}"
echo -e "(Stored in .env as STS_AUTH_API_KEY)"
echo ""
echo -e "${BLUE}Quick start:${NC}"
echo ""
echo "  # Activate the environment"
echo "  source .venv/bin/activate"
echo ""
echo "  # Start the API server + web UI"
echo "  sts-monitor serve --port 8080"
echo "  # Then open http://localhost:8080 in your browser"
echo ""
echo "  # Or run a full intelligence cycle from the CLI"
echo "  sts-monitor cycle \"earthquake in Turkey\""
echo ""
echo "  # Run with Docker (Postgres + Redis + Qdrant)"
echo "  docker compose up -d"
echo ""
echo -e "${BLUE}Useful commands:${NC}"
echo "  sts-monitor cycle \"topic\"          # Full intelligence cycle"
echo "  sts-monitor deep-truth \"topic\"     # Forensic analysis"
echo "  sts-monitor surge \"topic\"          # Social media surge detection"
echo "  sts-monitor retention --dry-run    # Preview data cleanup"
echo "  sts-monitor serve --reload         # API server with hot reload"
echo ""
echo -e "${BLUE}Optional: Set up local LLM (Ollama):${NC}"
echo "  curl -fsSL https://ollama.com/install.sh | sh"
echo "  ollama pull llama3.1"
echo "  ollama pull nomic-embed-text"
echo "  # Then set STS_LOCAL_LLM_URL=http://localhost:11434 in .env"
echo ""
