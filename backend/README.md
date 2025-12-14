# Studydy Backend (FastAPI)

Minimal FastAPI backend with SQLModel. Run everything from the `backend/` directory on WSL.

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Run the server
```bash
uvicorn app.main:app --reload
```

Database setup happens automatically on startup:
- Default: SQLite `sqlite:///./studydy.db` (file in `backend/`)
- Override with `DATABASE_URL` (example MySQL: `mysql+pymysql://user:password@localhost:3306/studydy`)
- JWT secret defaults to `dev-change-me`; override with `JWT_SECRET_KEY` for non-dev
- Optional: copy `.env.example` to `.env` and adjust values as needed.

## Auth workflow
- Password minimum length: 8 characters.
- Learning preferences allowed: `visual`, `text`, `ai_assisted`.

Endpoints:
- POST `/auth/register/request-code` → validate email/password, ensure email unused, generate 6-digit code (expires in 10 minutes), send via email service stub. Response: `{"detail": "Verification code sent"}`.
- POST `/auth/register/confirm` → body: `email`, `password`, `code`, optional `learning_preference`; verifies code then creates user. Response 201 with `id`, `email`, `learning_preference`, `created_at`.
- POST `/auth/login` → body: `email`, `password`; on success returns `{ "access_token": "<jwt>", "token_type": "bearer" }`. Use with header `Authorization: Bearer <token>`.
- GET `/auth/learning-preferences` → returns the allowed preference values.
- GET `/health` → `{"status": "ok"}`.
- GET `/` → `{"message": "Studydy backend running"}`.

## Run tests
```bash
pytest
```
