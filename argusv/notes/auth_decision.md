# Auth Decision — ArgusV

**Date:** 2026-03-08  
**Author:** BRAYAN (DEV-5)  
**Status:** Accepted

---

## Decision

**Direct auth — no reverse-proxy auth layer.**  
ArgusV handles authentication entirely inside the FastAPI backend. No Nginx/Traefik auth middleware is used.

## Context

ArgusV is a single-tenant local surveillance system. The user base is small (one admin per deployment). Adding an external auth proxy would increase infra complexity with no meaningful security benefit for the target deployment model. Keeping auth inside the app makes it simpler to develop, test, and deploy.

## Auth Flow

1. **Login endpoint:** `POST /api/auth/token`
   - Accepts `{ "username": "...", "password": "..." }` (JSON body)
   - Returns `{ "access_token": "<jwt>", "token_type": "bearer" }`

2. **Header format:** `Authorization: Bearer <token>`
   - All protected API routes use FastAPI's `HTTPBearer` dependency
   - Current stub (`get_current_user`) returns anonymous admin — to be replaced by AUTH-01

3. **Token details:**
   - Algorithm: HS256
   - Secret: `JWT_SECRET` env var (default `"change-me-in-production"`)
   - Expiry: `JWT_EXPIRE_MINUTES` env var (default 60 minutes)
   - Library: `python-jose` or `PyJWT` (AUTH-01 will choose)

4. **Frontend wiring (AUTH-03 — my scope):**
   - Login page sends `POST /api/auth/token`, stores JWT in `localStorage`
   - Every `fetch()` call attaches `Authorization: Bearer <token>` header
   - On `401` response → redirect to `/login`
   - Dashboard, incidents, zones, recordings pages all check for token on load

## What This Does NOT Cover

| Item | Owner | Ticket |
|------|-------|--------|
| Backend JWT creation/verification | DEV-3 | AUTH-01 |
| API key auth (service-to-service) | DEV-3 | AUTH-06 |
| Refresh tokens | DEV-3 | AUTH-07 |
| Role-based access control | Future | — |

## Current State of Code

- `src/auth/jwt_handler.py` — `create_access_token()` and `verify_token()` raise `NotImplementedError`
- `get_current_user()` returns `{"user": "anonymous", "role": "ADMIN"}` for all requests (no actual auth)
- `SECRET_KEY`, `ALGORITHM`, `TOKEN_EXPIRE` constants already defined
- `HTTPBearer` security scheme is registered but unused

## Alternatives Considered

| Option | Verdict |
|--------|---------|
| Nginx auth_request module | Rejected — adds deployment dependency, overkill for single-tenant |
| Session cookies | Rejected — harder to use with WebSocket + REST mix |
| OAuth2 / OIDC provider | Rejected — no external identity provider needed for local deploy |
| **JWT Bearer (direct)** | **Accepted** — simple, stateless, works with both REST and WS |
