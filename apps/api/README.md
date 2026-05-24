# @qa/api

FastAPI backend for QAtestbuddy.

## Dev

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
python -m uvicorn app.main:app --reload --port 8000
```

Health check: http://localhost:8000/health
