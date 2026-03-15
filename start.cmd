@echo off
cd /d C:\Users\User\Desktop\STSIA
.venv\Scripts\uvicorn.exe sts_monitor.main:app --port 8080
