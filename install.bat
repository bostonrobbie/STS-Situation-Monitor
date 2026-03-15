@echo off
REM ──────────────────────────────────────────────────────────────
REM STS Situation Monitor — Desktop Installer (Windows)
REM ──────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion

echo.
echo ========================================================
echo       STS Situation Monitor — Desktop Setup (Windows)
echo ========================================================
echo.

REM ── 1. Check Python ──────────────────────────────────────────
echo [INFO] Checking Python version...
python --version >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Python is not installed or not in PATH.
    echo        Download from https://www.python.org/downloads/
    echo        IMPORTANT: Check "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%V in ('python --version 2^>^&1') do set PYVER=%%V
for /f "tokens=1,2 delims=." %%A in ("%PYVER%") do (
    set PYMAJOR=%%A
    set PYMINOR=%%B
)

if %PYMAJOR% LSS 3 (
    echo [FAIL] Python 3.11+ required, found %PYVER%
    pause
    exit /b 1
)
if %PYMINOR% LSS 11 (
    echo [FAIL] Python 3.11+ required, found %PYVER%
    pause
    exit /b 1
)
echo [OK]   Found Python %PYVER%

REM ── 2. Create virtual environment ───────────────────────────
if not exist ".venv" (
    echo [INFO] Creating virtual environment...
    python -m venv .venv
    echo [OK]   Virtual environment created at .venv\
) else (
    echo [OK]   Virtual environment already exists
)

REM Activate
call .venv\Scripts\activate.bat

REM ── 3. Install dependencies ─────────────────────────────────
echo [INFO] Installing STS Situation Monitor and dependencies...
pip install --upgrade pip setuptools wheel -q
pip install -e ".[dev]" -q
echo [OK]   All dependencies installed

REM ── 4. Set up configuration ─────────────────────────────────
if not exist ".env" (
    echo [INFO] Creating .env from template...
    copy .env.example .env >nul

    REM Generate random API key
    for /f %%K in ('python -c "import secrets; print(secrets.token_urlsafe(32))"') do set API_KEY=%%K
    python -c "import sys; t=open('.env').read(); open('.env','w').write(t.replace('STS_AUTH_API_KEY=change-me','STS_AUTH_API_KEY=%API_KEY%'))"

    REM Generate random JWT secret
    for /f %%J in ('python -c "import secrets; print(secrets.token_urlsafe(48))"') do set JWT_SECRET=%%J
    python -c "import sys; t=open('.env').read(); open('.env','w').write(t.replace('STS_JWT_SECRET=change-me-in-production','STS_JWT_SECRET=%JWT_SECRET%'))"

    echo [OK]   Configuration file created (.env)
    echo [INFO] Your API key: %API_KEY%
    echo [WARN] Save this key — you'll need it to access the API.
) else (
    echo [OK]   Configuration file already exists (.env)
)

REM ── 5. Run database migrations ──────────────────────────────
echo [INFO] Running database migrations...
python -m alembic upgrade head 2>nul
if errorlevel 1 (
    echo [WARN] Migrations skipped (tables may already exist)
) else (
    echo [OK]   Database ready
)

REM ── 6. Verify installation ──────────────────────────────────
echo [INFO] Verifying installation...
python -c "from sts_monitor.main import app; print('[OK]   App loads OK')"
if errorlevel 1 (
    echo [FAIL] Installation verification failed.
    pause
    exit /b 1
)

REM ── Summary ─────────────────────────────────────────────────
echo.
echo ========================================================
echo             Installation Complete!
echo ========================================================
echo.
echo   Start the API server:
echo     .venv\Scripts\activate
echo     python -m sts_monitor serve --port 8080
echo.
echo   Open the dashboard:
echo     http://localhost:8080/static/index.html
echo.
echo   Run a CLI intelligence cycle:
echo     python -m sts_monitor cycle "earthquake in Turkey"
echo.
echo   Run tests:
echo     pytest -q
echo.
echo   Full documentation: see INSTALL.md and docs\
echo.
pause
