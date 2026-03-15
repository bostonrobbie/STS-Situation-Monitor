"""Entrypoint for PM2 — runs uvicorn programmatically."""
import sys
import os

# Ensure cwd is the project root so .env and DB paths resolve correctly
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import uvicorn

if __name__ == "__main__":
    uvicorn.run("sts_monitor.main:app", host="0.0.0.0", port=8080, reload=False)
