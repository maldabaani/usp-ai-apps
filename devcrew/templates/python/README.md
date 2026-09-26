# app

FastAPI service scaffolded by DevCrew.

```bash
pip install -e ".[dev]"
uvicorn app.main:app --reload
pytest -q
ruff check . && ruff format --check .
```
