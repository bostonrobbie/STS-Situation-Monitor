@echo off
title STS Local Intelligence - Central MA
echo ============================================
echo  STS LOCAL INTELLIGENCE - Central MA
echo ============================================
echo.
echo Starting API server on http://localhost:8080
echo Dashboard: http://localhost:8080/dashboard
echo.

cd /d "%~dp0"

REM Start the API server
start "STS API" cmd /c ".venv\Scripts\uvicorn sts_monitor.main:app --host 0.0.0.0 --port 8080"

timeout /t 5 /nobreak >nul

echo Server started. Running initial data ingestion...
echo.

REM Run initial ingest to populate dashboard
.venv\Scripts\python -c "import httpx,json;KEY='QRgeyN7CTsA5KaswD2OPZhgLgK1ma8x01bMrueQRaFY';H={'X-API-Key':KEY,'Content-Type':'application/json'};B='http://localhost:8080';invs=httpx.get(f'{B}/investigations',headers=H).json();inv_id=invs[0]['id'] if invs else None;print(f'Using investigation: {inv_id}');[(print(f'  {p}...'),httpx.post(f'{B}/investigations/{inv_id}{p}',headers=H,json=b,timeout=60)) for p,b in [('/ingest/nws',{}),('/ingest/fema',{}),('/ingest/usgs',{'min_magnitude':2.0}),('/ingest/google-news',{}),('/ingest/mbta',{}),('/ingest/ma-environment',{})] if inv_id]"

echo.
echo Initial data loaded. Starting auto-ingest worker...
echo.

REM Start auto-ingest in background
start "STS Auto-Ingest" cmd /c ".venv\Scripts\python auto_ingest.py"

echo.
echo ============================================
echo  ALL SYSTEMS RUNNING
echo  Dashboard: http://localhost:8080/dashboard
echo  API: http://localhost:8080/health
echo ============================================
echo.
echo Press any key to open dashboard in browser...
pause >nul
start http://localhost:8080/dashboard
