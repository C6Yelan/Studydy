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

## Run tests
```bash
pytest
```

## API routes (current)
- GET `/` → `{"message": "Studydy backend running"}`
- GET `/health` → `{"status": "ok"}`
- POST `/auth/register` → 501 Not Implemented (request: email, password, optional learning_preference)
- POST `/auth/login` → 501 Not Implemented (request: email, password)
