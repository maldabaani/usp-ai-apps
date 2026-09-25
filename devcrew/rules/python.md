# Python standards (FastAPI)

Rule ids are cited by the Reviewer as `rule_ref`. Edit freely; keep the `**ID**` markers.

## Structure
- **PY-001** Python 3.12, fully type-hinted public functions; no `Any` unless unavoidable.
- **PY-002** One `APIRouter` per resource in `app/routers/<resource>.py`, included from `app/main.py`.
- **PY-003** Request/response bodies are Pydantic v2 models in `app/schemas/`; never return ORM objects or raw dicts from endpoints.
- **PY-004** Business logic lives in `app/services/`, not in route functions. Routers only parse, call a service, and map errors.
- **PY-005** Use FastAPI dependency injection (`Depends`) for services, repositories and settings; no module-level mutable state.

## API behavior
- **PY-010** Correct status codes: 201 on create (with the created resource), 204 on delete, 404 for missing resources, 422 for validation.
- **PY-011** Raise `HTTPException` (or a mapped domain exception) with a clear `detail`; never leak stack traces.
- **PY-012** Validate inputs with Pydantic constraints (`Field(min_length=..., ge=...)`) instead of manual checks.

## Quality
- **PY-020** Code passes `ruff check` and `ruff format` with the template's config.
- **PY-021** No bare `except:`; catch specific exceptions.
- **PY-022** Configuration via `pydantic-settings`, never hardcoded secrets or URLs.

## Tests
- **PY-030** pytest with FastAPI `TestClient`; tests live in `tests/` and are named `test_<module>.py`.
- **PY-031** Tests are independent: fresh app/state per test via fixtures (override dependencies with `app.dependency_overrides`).
