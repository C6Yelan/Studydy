# Frontend Integration

This document summarizes how the frontend should talk to the backend. For full endpoint schemas and responses, use the OpenAPI docs at `/docs` (single source of truth).

## Base URL
Local default: `http://127.0.0.1:8000`

## Auth model (session cookie)
- The backend uses **session cookies** (Starlette `SessionMiddleware`), not JWT.
- Send requests with `credentials: "include"` so the browser stores and returns the session cookie.

Example helper:
```js
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export async function apiFetch(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    credentials: "include",
    ...options,
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw data;
  return data;
}
```

## Key flows (summary)
- **Register**: `POST /auth/register` → creates user and sets session cookie.
- **Login**: `POST /auth/login` → sets session cookie (generic error on failure).
- **Session**: `GET /auth/me` and `POST /auth/logout`.
- **Password reset**: `POST /auth/password-reset/request-code` → `POST /auth/password-reset/confirm`.

## Materials & Dashboard
Endpoints:
- `GET /materials/me`
- `POST /materials/upload` (multipart/form-data, field name: `file`)

Dashboard example (JSON):
```js
const me = await apiFetch("/materials/me");
```

Upload example (FormData):
```js
const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

export async function apiUpload(path, file) {
  const form = new FormData();
  form.append("file", file);

  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    body: form,
    credentials: "include",
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw data;
  return data;
}
```

Notes:
- Do not manually set `Content-Type` for multipart uploads; the browser will add the correct boundary.
- Always send `credentials: "include"` (session cookie auth).

## Password reset in dev/test
- Dev behavior: verification code is printed to backend stdout (ConsoleEmailService).
- Tests: fake email service captures codes (no console scraping).

## CORS notes
- Cookies require `allow_credentials=true` on the backend (already enabled).
- Configure `CORS_ORIGINS` (comma-separated) to match your frontend origin.

## Use OpenAPI for details
Only keep brief summaries here; rely on `/docs` for the latest request/response schemas and error details.
