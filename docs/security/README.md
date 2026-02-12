# Security Notes

This document summarizes current security behavior and known gaps in the backend, focused on auth and password reset. It reflects **current implementation**, not aspirational design.

## Scope
- Login, registration, password reset
- Session cookies
- Secrets handling

## Account enumeration
### Implemented
- **Login** returns a generic error (`Incorrect email or password`).
- **Password reset request-code** always returns `200` with a generic message.

### Known gap
- **Register** currently returns a specific `400` error (`Email already registered`) when the email exists. This leaks account existence. Treat it as a known limitation until the flow is redesigned.

## Password reset flow
- Two-step flow: request-code → confirm.
- Codes are generated with secure randomness, have expiry, and are stored as hashes (not plaintext).
- Invalid/expired codes return a generic error (`Invalid or expired verification code`).
- Dev email behavior: code is printed to stdout (ConsoleEmailService).
- Tests use a fake email service (no stdout dependency).

## Session cookies
- Auth is session-cookie based (not JWT).
- Session signing secret comes from `SESSION_SECRET_KEY` (must be strong in production).
- Current defaults are for dev only; production should ensure HTTPS-only cookies (requires code/config changes if not already enforced).

## Secrets hygiene
- Never commit `.env`, API keys, DB strings, or private keys.
- `.env.example` should contain placeholders only.

## TODO / hardening ideas (not yet implemented)
- Rate limiting for login and reset flows.
- Session rotation / global logout.
- MFA.
- Centralized secrets manager.

## References
- OWASP Authentication Cheat Sheet
- OWASP Forgot Password Cheat Sheet
- OWASP WSTG: Account Enumeration & Session Management
