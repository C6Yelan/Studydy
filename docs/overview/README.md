# Studydy Overview

Studydy converts user-uploaded learning materials (notes, handouts, articles) into story-driven, interactive learning content. This repo currently focuses on the backend; the frontend prototype exists but is not yet connected to the backend.

## What exists today
- Backend: FastAPI + SQLModel with session-cookie authentication (SessionMiddleware).
- Frontend: prototype only (`fe/m3-prototype`), no backend integration yet.
- API docs: `/docs` (OpenAPI) is the single source of truth for endpoints and schemas.

## Where to start
- Backend development: `docs/backend/README.md`
- Frontend integration: `docs/frontend/README.md`
- Deployment notes: `docs/deployment/README.md`
- Security notes and current limitations: `docs/security/README.md`
- Contribution rules (including secrets hygiene): `docs/contributing/README.md`

## Quick references
- Local OpenAPI/Swagger: `http://127.0.0.1:8000/docs`
- Health check: `http://127.0.0.1:8000/health`
