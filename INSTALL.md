# STS Situation Monitor — Desktop Deployment Guide

Complete instructions for downloading, installing, and running the STS Situation Monitor on a Windows, macOS, or Linux desktop PC.

---

## Table of Contents

1. [System Requirements](#system-requirements)
2. [Quick Install (Automated)](#quick-install-automated)
3. [Manual Install — Windows](#manual-install--windows)
4. [Manual Install — macOS](#manual-install--macos)
5. [Manual Install — Linux](#manual-install--linux)
6. [Configuration](#configuration)
7. [Starting the Application](#starting-the-application)
8. [Dashboard Access](#dashboard-access)
9. [Optional: Local LLM (Ollama)](#optional-local-llm-ollama)
10. [Optional: Full Infrastructure Stack (Docker)](#optional-full-infrastructure-stack-docker)
11. [Running Tests](#running-tests)
12. [CLI Commands](#cli-commands)
13. [Backup and Restore](#backup-and-restore)
14. [Upgrading](#upgrading)
15. [Uninstalling](#uninstalling)
16. [Troubleshooting](#troubleshooting)

---

## System Requirements

### Minimum

| Component | Requirement |
|-----------|-------------|
| **OS** | Windows 10+, macOS 12+, or Linux (Ubuntu 22.04+, Fedora 38+, Debian 12+) |
| **Python** | 3.11 or 3.12 |
| **RAM** | 4 GB |
| **Disk** | 500 MB (application + dependencies) |
| **Network** | Internet for OSINT data collection (optional — works offline with simulated data) |

### Recommended (for LLM + full stack)

| Component | Requirement |
|-----------|-------------|
| **RAM** | 16 GB+ (for local LLM models) |
| **Disk** | 10 GB+ (for LLM models + vector database) |
| **GPU** | Optional — Ollama uses CPU if no GPU available |
| **Docker** | For Postgres + Redis + Qdrant stack |

### What you get without optional components

| Component | Without it | With it |
|-----------|-----------|---------|
| **Ollama** | All analysis works via deterministic algorithms (no LLM summaries) | AI-powered entity extraction, summaries, and structured analysis |
| **Docker** | SQLite database (fully functional) | PostgreSQL + Redis job queue + Qdrant vector search |
| **API keys** | Free-tier connectors work (GDELT, USGS, NWS, Reddit, RSS) | Full access to NASA FIRMS, ACLED, ADS-B, MarineTraffic |

---

## Quick Install (Automated)

### Linux / macOS

```bash
git clone https://github.com/your-org/STS-Situation-Monitor.git
cd STS-Situation-Monitor
chmod +x install.sh
./install.sh
```

### Windows

```cmd
git clone https://github.com/your-org/STS-Situation-Monitor.git
cd STS-Situation-Monitor
install.bat
```

The installer will:
- Verify Python 3.11+ is installed
- Create a virtual environment (`.venv/`)
- Install all dependencies
- Generate a secure API key and JWT secret
- Create the `.env` configuration file
- Run database migrations
- Print quick-start instructions

---

## Manual Install — Windows

### Step 1: Install Python

1. Download Python 3.12 from https://www.python.org/downloads/
2. **IMPORTANT:** Check **"Add Python to PATH"** during installation
3. Open Command Prompt and verify:
   ```cmd
   python --version
   ```

### Step 2: Get the code

```cmd
git clone https://github.com/your-org/STS-Situation-Monitor.git
cd STS-Situation-Monitor
```

Or download and extract the ZIP file.

### Step 3: Create virtual environment

```cmd
python -m venv .venv
.venv\Scripts\activate
```

### Step 4: Install dependencies

```cmd
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

### Step 5: Configure

```cmd
copy .env.example .env
python scripts\generate_api_key.py
```

Edit `.env` in a text editor and paste the generated key as `STS_AUTH_API_KEY`.

### Step 6: Set up database

```cmd
python -m alembic upgrade head
```

### Step 7: Start the server

```cmd
python -m sts_monitor serve --port 8080
```

Open http://localhost:8080/static/index.html in your browser.

---

## Manual Install — macOS

### Step 1: Install Python

```bash
brew install python@3.12
```

Or download from https://www.python.org/downloads/

### Step 2: Get the code

```bash
git clone https://github.com/your-org/STS-Situation-Monitor.git
cd STS-Situation-Monitor
```

### Step 3: Create virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 4: Install dependencies

```bash
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

### Step 5: Configure

```bash
cp .env.example .env
python scripts/generate_api_key.py
# Edit .env and paste the key as STS_AUTH_API_KEY
```

### Step 6: Set up database

```bash
python -m alembic upgrade head
```

### Step 7: Start the server

```bash
python -m sts_monitor serve --port 8080
```

Open http://localhost:8080/static/index.html in your browser.

---

## Manual Install — Linux

### Step 1: Install Python

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip git

# Fedora
sudo dnf install python3.11 git

# Arch
sudo pacman -S python git
```

### Step 2: Get the code

```bash
git clone https://github.com/your-org/STS-Situation-Monitor.git
cd STS-Situation-Monitor
```

### Step 3: Create virtual environment

```bash
python3.11 -m venv .venv
source .venv/bin/activate
```

### Step 4: Install dependencies

```bash
pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
```

### Step 5: Configure

```bash
cp .env.example .env
python scripts/generate_api_key.py
# Edit .env and paste the key as STS_AUTH_API_KEY
```

### Step 6: Set up database

```bash
python -m alembic upgrade head
```

### Step 7: Start the server

```bash
python -m sts_monitor serve --port 8080
```

Open http://localhost:8080/static/index.html in your browser.

---

## Configuration

All configuration is in the `.env` file. Key settings:

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `STS_ENV` | `dev` | Environment (`dev`, `prod`) |
| `STS_API_PORT` | `8080` | API server port |
| `STS_DATABASE_URL` | `sqlite:///./sts_monitor.db` | Database URL |
| `STS_AUTH_API_KEY` | `change-me` | Root API key (generate a secure one!) |
| `STS_ENFORCE_AUTH` | `true` | Require API key for all requests |
| `STS_JWT_SECRET` | `change-me-in-production` | JWT signing secret |

### Optional Data Source Keys

Most connectors work without API keys. For full coverage:

| Variable | Source | How to get |
|----------|--------|-----------|
| `STS_NASA_FIRMS_MAP_KEY` | NASA fire data | https://firms.modaps.eosdis.nasa.gov/api/area/ |
| `STS_ACLED_API_KEY` | Conflict data | https://developer.acleddata.com/ |
| `STS_ADSB_API_KEY` | Aircraft tracking | https://www.adsbexchange.com/data/ |
| `STS_MARINE_API_KEY` | Ship tracking | https://www.marinetraffic.com/en/ais-api-services |
| `STS_WINDY_API_KEY` | Webcams | https://api.windy.com/webcams |

### Local LLM Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `STS_LOCAL_LLM_URL` | `http://localhost:11434` | Ollama endpoint |
| `STS_LOCAL_LLM_MODEL` | `llama3.1` | Model name |
| `STS_LOCAL_LLM_TIMEOUT_S` | `10` | LLM request timeout |

See `.env.example` for the full list of 160+ configuration options.

---

## Starting the Application

### API Server (recommended)

```bash
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

python -m sts_monitor serve --port 8080
```

The server starts at http://localhost:8080. Key URLs:

| URL | Description |
|-----|-------------|
| http://localhost:8080/static/index.html | Main dashboard |
| http://localhost:8080/static/dashboard.html | Investigation dashboard |
| http://localhost:8080/static/globe.html | Geospatial visualization |
| http://localhost:8080/docs | Swagger API documentation |
| http://localhost:8080/health | Health check |

### CLI Mode (no server needed)

```bash
python -m sts_monitor cycle "earthquake in Turkey"
python -m sts_monitor deep-truth "lab leak theory" --claim "virus was engineered"
python -m sts_monitor surge "Ukraine conflict" --categories conflict,osint
```

---

## Dashboard Access

Once the server is running, open the main dashboard:

```
http://localhost:8080/static/index.html
```

All API calls require the `X-API-Key` header:

```bash
curl -H "X-API-Key: YOUR_KEY" http://localhost:8080/investigations
```

The dashboard handles authentication automatically when configured.

---

## Optional: Local LLM (Ollama)

Ollama provides local AI processing without sending data to external services.

### Install Ollama

```bash
# Linux
curl -fsSL https://ollama.com/install.sh | sh

# macOS
brew install ollama

# Windows
# Download from https://ollama.ai/download
```

### Pull required models

```bash
ollama pull llama3.1              # For analysis and summaries
ollama pull nomic-embed-text      # For semantic search embeddings
```

### Configure

Ensure these are set in `.env`:

```
STS_LOCAL_LLM_URL=http://localhost:11434
STS_LOCAL_LLM_MODEL=llama3.1
STS_EMBEDDING_MODEL=nomic-embed-text
```

### Verify

```bash
python -m sts_monitor serve --port 8080
# Visit http://localhost:8080/system/preflight
# The LLM section should show "connected"
```

---

## Optional: Full Infrastructure Stack (Docker)

For production-grade deployment with PostgreSQL, Redis, and Qdrant:

### Prerequisites

Install Docker and Docker Compose: https://docs.docker.com/get-docker/

### Start the stack

```bash
# Development (local access only)
docker compose up -d

# Production with HTTPS (public internet)
docker compose -f docker-compose.public.yml up -d
```

### Services

| Service | Port | Purpose |
|---------|------|---------|
| API | 8080 | FastAPI application |
| PostgreSQL | 5432 | Production database |
| Redis | 6379 | Job queue and caching |
| Qdrant | 6333 | Vector search (semantic) |

### Update .env for Docker

```
STS_DATABASE_URL=postgresql://sts:sts@localhost:5432/sts
STS_REDIS_URL=redis://localhost:6379/0
STS_QDRANT_URL=http://localhost:6333
```

---

## Running Tests

```bash
source .venv/bin/activate

# Full test suite (1738 tests, ~3 minutes)
pytest -q

# With coverage report
pytest --cov=sts_monitor --cov-fail-under=75

# By category
pytest -m unit                    # Fast unit tests
pytest -m integration             # API/database tests
pytest -m simulation              # Full workflow simulations

# Stress tests
pytest tests/test_stress_api.py tests/test_stress_analysis.py --no-cov

# Full QA gate
./scripts/qa_local.sh
```

---

## CLI Commands

```bash
# Full intelligence cycle
python -m sts_monitor cycle "earthquake in Turkey"
python -m sts_monitor cycle "earthquake in Turkey" --json --report-file report.md
python -m sts_monitor cycle "topic" --no-social --min-reliability 0.6

# Deep forensic analysis
python -m sts_monitor deep-truth "lab leak theory" --claim "virus was engineered"
python -m sts_monitor deep-truth "election interference" --json

# Social media surge detection
python -m sts_monitor surge "Ukraine conflict" --categories conflict,osint

# Data retention cleanup
python -m sts_monitor retention --max-age 90 --dry-run
python -m sts_monitor retention --max-age 30

# API server
python -m sts_monitor serve --port 8080
python -m sts_monitor serve --host 0.0.0.0 --port 8080 --reload
```

---

## Backup and Restore

### Database backup

```bash
python scripts/db_backup.py                          # Creates timestamped backup
python scripts/db_restore.py backups/<filename>.db    # Restore from backup
```

### Repository backup

```bash
python scripts/repo_backup.py              # Git bundle with all history
python scripts/repo_backup.py --verify     # Create + verify integrity
```

---

## Upgrading

```bash
cd STS-Situation-Monitor
git pull origin main

source .venv/bin/activate
pip install -e ".[dev]"
python -m alembic upgrade head

# Verify
pytest -q
```

---

## Uninstalling

```bash
# Remove virtual environment
rm -rf .venv/

# Remove database
rm -f sts_monitor.db

# Remove configuration
rm -f .env

# Remove plugin directory
rm -rf ~/.sts-monitor/

# Remove the entire project
cd ..
rm -rf STS-Situation-Monitor/
```

---

## Troubleshooting

### "Python not found" or wrong version

Ensure Python 3.11+ is installed and in your PATH:
```bash
python3 --version    # Linux/macOS
python --version     # Windows
```

### "ModuleNotFoundError"

Make sure the virtual environment is activated:
```bash
source .venv/bin/activate    # Linux/macOS
.venv\Scripts\activate       # Windows
```

### "alembic: command not found"

Run via Python module:
```bash
python -m alembic upgrade head
```

### Database locked (SQLite)

Only one process can write to SQLite at a time. Stop other instances:
```bash
# Find processes using the DB
lsof sts_monitor.db    # Linux/macOS
```

### Port 8080 already in use

Use a different port:
```bash
python -m sts_monitor serve --port 9090
```

### LLM not responding

1. Verify Ollama is running: `ollama list`
2. Check the URL in `.env`: `STS_LOCAL_LLM_URL=http://localhost:11434`
3. Test directly: `curl http://localhost:11434/api/tags`

### Docker containers not starting

```bash
docker compose down
docker compose up -d --build
docker compose logs api    # Check for errors
```

### Tests failing

```bash
# Run with verbose output
pytest -v --tb=long

# Run a specific test
pytest tests/test_api.py -v
```

### Need help?

- Check the Swagger docs at http://localhost:8080/docs
- Run preflight check: http://localhost:8080/system/preflight
- Read `docs/offline-buildout-checklist.md` for detailed wiring verification
- File issues at the project repository
