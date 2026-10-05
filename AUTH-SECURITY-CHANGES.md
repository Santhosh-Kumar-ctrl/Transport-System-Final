# Auth module security changes

Base: `main` at 08441bad. Branch: `auth-hardening`. Backend tests: 83 pass on PostgreSQL (80 existing + earlier security regressions + 3 new throttle/self-deactivate tests).

## What changed

| # | Area | Problem | Fix | Files |
|---|------|---------|-----|-------|
| 1 | JWT secret | Public fallback signing key in `config.py`; anyone could forge admin tokens if deployed with it | `JWT_SECRET` is now required (min 32 chars) and known placeholder values are rejected at startup | `core/config.py`, `.env.example` |
| 2 | Refresh tokens | Reusable, and still valid after a password reset | One-use refresh sessions stored as SHA-256 digests, atomic rotation (delete-returning), replay rejected | `auth/models.py`, `auth/service.py`, migration `20261003_security_sessions.py` |
| 3 | Revocation | Deactivated user kept access up to 30 min; password reset did not kill old tokens | `token_version` claim checked against DB on every request; bumped on deactivate or password change; all refresh sessions deleted | `core/deps.py`, `core/security.py`, `auth/service.py` |
| 4 | WebSockets | Sockets outlived token expiry/deactivation; token in URL; any topic subscribable | Token sent in first frame, rechecked against DB, topic authorization | `core/realtime.py`, `core/access.py`, `frontend/.../realtime.dart` |
| 5 | Authorization | Any logged-in user could read other trips, GPS, driver phone | Object-level access checks | `trips/router.py`, `tracking/router.py` |
| 6 | Passwords | bcrypt silently ignores bytes after 72 | Validate UTF-8 byte length on create/update/login; verify refuses >72 bytes | `auth/schemas.py`, `core/security.py` |
| 7 | Demo password | Admin screen prefilled a shared password | Prefill removed | `admin_people_screen.dart` |
| 8 | **Login throttling (new)** | Unlimited password guessing | Failed logins counted per email (5) and per IP (50) in a 15 min window, then `429` with `Retry-After`. Success clears the email counter. Settings `LOGIN_MAX_FAILURES`, `LOGIN_LOCKOUT_SECONDS` | `core/throttle.py`, `auth/router.py`, `core/errors.py`, `main.py` |
| 9 | **Timing leak (new)** | Unknown email returned faster than wrong password, revealing which emails exist | Dummy bcrypt check for unknown emails | `auth/service.py` |
| 10 | **Create race (new)** | Concurrent duplicate create could surface a 500 | `IntegrityError` mapped to `409` | `auth/service.py` |
| 11 | **Self-lockout (new)** | An admin could deactivate their own account | Rejected with `422 self_deactivate` | `auth/service.py` |

## Deploy notes

- Set a fresh `JWT_SECRET` (`python -c "import secrets;print(secrets.token_urlsafe(48))"`). The app will not start without it. Rotate it if the old default was ever deployed.
- Run the Alembic migration (`alembic upgrade head`). It was tested upgrade, downgrade, re-upgrade on PostgreSQL.
- Deploy backend and frontend together: the WebSocket handshake changed.
- Existing sessions are logged out once (refresh tokens now need a server-side session).

## Still not fixed

- Throttle state is in process memory. With several workers the limit multiplies; add a Redis or reverse-proxy limiter for production. Behind a proxy, run uvicorn with `--proxy-headers` and `--forwarded-allow-ips`, otherwise all clients share one IP.
- Tokens are stored in `shared_preferences`/localStorage in the Flutter app. Use `flutter_secure_storage` on mobile and a deliberate cookie + CSRF design on web.
- No MFA, no email-based password reset flow.
- Demo seed accounts (`scripts/seed.py`) use shared public passwords; remove or force reset in any real deployment.
- Android release allows HTTP; default docker-compose/DB passwords; use TLS and real secrets.
- Flutter code was not compiled (no Flutter toolchain here); only the backend was tested.

## Scanning

Semgrep (p/security-audit + p/secrets) reported zero findings before and after. It has no Dart coverage and cannot see logic flaws, so the items above came from manual review. Zero findings is not a safety verdict.
