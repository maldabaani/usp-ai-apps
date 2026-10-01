# Python standards (FastAPI)

Rule ids are cited by the Reviewer as `rule_ref`. Edit freely; keep the `**ID**` markers.

These rules assume a FastAPI service. Before citing PY-002/003/004/005/010/011/012 or PY-030's
`TestClient` requirement, check whether the project actually has one (a FastAPI `app`,
`app/routers/`, HTTP endpoints) -- a plain library or domain-model project with no HTTP layer
has nothing for those rules to apply to, and raising a plain `ValueError`/domain exception for
invalid input at a function boundary (not an HTTP response) is correct there, not a PY-011
violation. Match the project's own existing convention (e.g. an existing function already
raising `ValueError` for bad input) over a rule that assumes a layer this project doesn't have.

## Structure
- **PY-001** Python 3.12, fully type-hinted public functions; no `Any` unless unavoidable.
- **PY-002** One `APIRouter` per resource in `app/routers/<resource>.py`, included from `app/main.py`. (FastAPI services only.)
- **PY-003** Request/response bodies are Pydantic v2 models in `app/schemas/`; never return ORM objects or raw dicts from endpoints. (FastAPI services only.)
- **PY-004** Business logic lives in `app/services/`, not in route functions. Routers only parse, call a service, and map errors. (FastAPI services only -- a project with no routes has no route functions to keep logic out of.)
- **PY-005** Use FastAPI dependency injection (`Depends`) for services, repositories and settings; no module-level mutable state. (FastAPI services only.)

## API behavior
- **PY-010** Correct status codes: 201 on create (with the created resource), 204 on delete, 404 for missing resources, 422 for validation. (FastAPI services only.)
- **PY-011** Raise `HTTPException` (or a mapped domain exception) with a clear `detail`; never leak stack traces. (FastAPI services only -- a plain `ValueError` at a non-HTTP function boundary is correct, not a violation.)
- **PY-012** Validate inputs with Pydantic constraints (`Field(min_length=..., ge=...)`) instead of manual checks. (FastAPI services only.)

## Quality
- **PY-020** Code passes `ruff check` and `ruff format` with the template's config.
- **PY-021** No bare `except:`; catch specific exceptions.
- **PY-022** Configuration via `pydantic-settings`, never hardcoded secrets or URLs.

## Tests
- **PY-030** pytest; tests live in `tests/` and are named `test_<module>.py`. Use FastAPI `TestClient` only when the project has HTTP endpoints to call.
- **PY-031** Tests are independent: fresh app/state per test via fixtures (override dependencies with `app.dependency_overrides`).
