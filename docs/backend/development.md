# Backend Architecture & Testing

This document focuses on backend structure, extension points, and testing strategy. All commands assume `backend/`.

## Architecture principles
- **Router layer** (`app/routers/`): route definitions, `Depends`, and `HTTPException` only.
- **Schema layer** (`app/schemas/`): Pydantic request/response models.
- **Core layer** (`app/core/`): configuration constants and security helpers (hash/verify).
- **Service layer** (`app/services/`): reusable workflows (e.g., verification codes, email service abstraction).
- **Model layer** (`app/models/`): SQLModel tables and constraints.

Keep router files thin; when adding features, extend services or core rather than embedding logic in routes.

## Project layout
- `app/main.py`: FastAPI app, middleware, routers, lifespan for DB setup.
- `app/db.py`: engine + `create_db_and_tables()` + `get_session()` dependency.
- `app/routers/`: `root.py`, `health.py`, `auth.py`.
- `app/models/`: `user.py`, `verification.py`.
- `app/schemas/`: auth request/response models.
- `app/core/`: config constants and security utilities.
- `app/services/`: `email.py` (console stub), `verification_codes.py` workflow.
- `tests/`: pytest tests with dependency overrides.

## Configuration notes
- Security and workflow constants live in `app/core/config.py`.
- Session/CORS settings are read in `app/main.py` from env vars.

## Testing strategy (pytest + anyio + httpx)
Tests use `httpx.AsyncClient` with `ASGITransport` and `pytest.mark.anyio`.

### Dependency overrides
- Override `get_session()` to inject a test database session.
- Override `get_email_service()` to use a fake email service (no stdout parsing).
- Always clear `app.dependency_overrides` after each test fixture.

### Recommended fixture pattern
- Use `yield` fixtures for setup/teardown.
- Ensure overrides and any temporary DB engine are reset in teardown.

### Common test commands
```bash
pytest
pytest -q
pytest -q -k auth
pytest -q tests/test_auth_login.py
```

## Pitfalls & troubleshooting
- **Flaky tests across files**: dependency overrides not cleared in teardown.
- **SQLite in-memory not persisting**: ensure all sessions use the same engine/connection scope.
- **Router bloat**: add logic in `services/` or `core/` instead.
