# Deployment (Minimal)

This document describes the minimal deployment path (Ubuntu + venv + Uvicorn) and reverse-proxy considerations. It intentionally avoids Docker/systemd/CI for now.

## Prerequisites
- Python 3.10+
- `python3-venv` and `pip`
- A reverse proxy (optional but recommended for TLS)

## Get the code
Use your team’s normal SCM workflow to obtain the repo on the server.

## Install and run
From `backend/`:
```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt
```

Set environment variables (at minimum, session secret):
```bash
export SESSION_SECRET_KEY="replace-me-with-a-strong-secret"
export CORS_ORIGINS="https://your-frontend.example"
# Optional: DATABASE_URL, defaults to SQLite if unset
```

Start the service:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Verify:
- Swagger/OpenAPI: `http://<server-ip>:8000/docs`
- Health: `curl http://127.0.0.1:8000/health`

## Multi-worker (optional)
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
```

## Reverse proxy notes
### Forwarded headers
If you terminate TLS at a proxy, enable proxy headers and restrict trusted IPs:
```bash
uvicorn app.main:app \
  --host 0.0.0.0 --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips="127.0.0.1"
```

Use `*` only when the backend is unreachable from the public internet.

### Path prefix (e.g., `/api`)
If the proxy strips a prefix, set `root_path` so Swagger/OpenAPI renders correct URLs:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --root-path /api
```

### Minimal Nginx snippet
```nginx
location /api/ {
  proxy_pass http://127.0.0.1:8000/;
  proxy_set_header Host $host;
  proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;
}
```
