# Studydy Backend (FastAPI)

Minimal FastAPI skeleton for the Studydy backend. Run everything from the `backend/` directory on WSL.

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

The database (SQLite by default) is created on startup if it does not exist:
- Default path: `sqlite:///./studydy.db` (file `studydy.db` in `backend/`)
- Override with `DATABASE_URL` environment variable (e.g., `export DATABASE_URL=sqlite:///./my.db`)
- JWT secret: defaults to `dev-change-me`; override in non-dev with `JWT_SECRET_KEY` (e.g., `export JWT_SECRET_KEY=your-secret`)

## Run tests
```bash
pytest
```

## API routes (current)
- GET `/` → `{"message": "Studydy backend running"}`
- GET `/health` → `{"status": "ok"}`
- POST `/auth/register` → 201 Created (request: email, password, optional learning_preference; returns id/email/learning_preference/created_at)
- POST `/auth/login` → 200 OK (request: email, password; returns access_token and token_type)
