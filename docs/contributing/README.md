# Contributing

## Ground rules
- Keep changes focused and reviewable.
- Run backend tests before opening a PR.
- Do not commit secrets (`.env`, API keys, DB URLs, private keys, certificates).
- Do not touch `docs_local/` (private, ignored).
- Do not commit runtime artifacts: `uploads/`, `*.db`.

## PR expectations
- One purpose per PR.
- Include tests or explain why they are unnecessary.
- Update docs in `docs/` when behavior or configuration changes.

## Docs locations
- Overview: `docs/overview/README.md`
- Backend: `docs/backend/README.md`
- Frontend integration: `docs/frontend/README.md`
- Deployment: `docs/deployment/README.md`
- Security: `docs/security/README.md`
