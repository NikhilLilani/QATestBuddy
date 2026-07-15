@echo off
cd /d "%~dp0..\apps\api"
"%~dp0..\apps\api\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000
