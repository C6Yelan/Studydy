# Backend Development

This guide covers local setup and backend-specific notes. Run all commands from `backend/`.

## Quickstart
```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt

uvicorn app.main:app --reload
```

- OpenAPI/Swagger: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/health`

## Configuration
Environment variables are optional for local dev unless noted.

- `SESSION_SECRET_KEY` (required in production): session cookie signing secret.
- `DATABASE_URL` (optional): defaults to SQLite if unset.
- `CORS_ORIGINS` (optional): comma-separated list of allowed origins for cross-site requests with cookies.

See `.env.example` for a template. Never commit real secrets.

## Auth model (current)
- Auth uses **session cookies** via Starlette `SessionMiddleware` (not JWT).
- `/auth/register` and `/auth/login` both establish a session cookie.
- `/auth/me` and `/auth/logout` rely on the session cookie.

## Password reset flow (current)
- Two-step flow: `request-code` → `confirm`.
- Dev behavior: `ConsoleEmailService` prints the verification code to server stdout.
- Tests: a fake email service captures codes (no console dependency).

## Next docs
- Architecture, layering, and testing strategy: `docs/backend/development.md`
