# auth: dev log

## 2026-10-04: Review fixes (Review.md)
**Built**
- bcrypt runs in a worker thread (B1), with a dummy check for unknown emails (L10). Login also
  releases its DB connection before hashing, so a login burst doesn't starve the pool.
- `users.token_version` + `ver` claim: a password change or deactivation revokes sessions (M2), and
  `UserSessionsRevoked` closes open sockets. Auth registers the WebSocket session check.
- Login rate limit, 10/min per IP + email (M3). The last active admin can't be deactivated (M6).
- A duplicate roll/licence no on PATCH is a 409 `profile_taken`, not a 500 (M1). `limit`/`offset` bounds.

**Decisions (and why)**
- Revocation is checked on refresh and WebSocket connect, not on every REST call: that keeps
  `Principal` DB-free, and the 30-minute access token bounds the gap.

## 2026-10-02: Security and parent roles removed
**Changed**
- `Role` is now `student`, `driver`, `admin`. Endpoints that allowed admin or security are admin-only,
  and live map and ops pushes go to admins only. `student_profiles.parent_user_id` is gone.
- Migration `9d3e5b7c1a2f` deletes existing security/parent accounts and narrows the `users.role`
  CHECK. Downgrade restores the column and the role list, not the deleted accounts.
- App: the parent placeholder screen and the security-only admin menu are gone. The People screen
  offers the three remaining roles.

**Why**
- Neither role had screens or workflows of its own, so they only added permission branches to
  maintain. If a parent view is needed later, add it back as a new role with its own profile table.

## 2026-09-24: Flutter screens
**Built**
- Login screen (sign-blue station sign + route-colour band), session persisted in shared_preferences,
  JWT refresh-on-401 in the Dio client, role-guarded routing (`app.dart`), admin People screen
  (filter by role, search, add person, deactivate).

**Decisions (and why)**
- **Router redirect is the single role guard**, so every role stays inside its own area.
- **Security staff reuse the admin shell** with only Live board / Reports / Alerts visible. Their
  backend permissions are read-only, so write screens would only 403.
- Tokens live in shared_preferences (localStorage on web). Move to flutter_secure_storage on Android
  before production.

## 2026-09-24: P0 auth module
**Built**
- `users` + `student_profiles` + `driver_profiles`, JWT login/refresh/me, admin user CRUD.
- `core/deps.py` `require_roles()` used by every other module.

**Decisions (and why)**
- **No DB hit per request.** The access token carries `sub` + `role`, which keeps every request cheap.
  Trade-off: deactivation takes effect when the access token expires; refresh is blocked immediately.
- **Profiles as separate 1:1 tables**, not nullable columns on `users`. That keeps role-specific
  fields out of the way and lets Team B add e.g. a parent profile without a wide table.
- **Enums stored as VARCHAR + CHECK** (`str_enum`), not native PG enums, because adding a role
  later is a one-line migration instead of `ALTER TYPE`.
- **Emails lower-cased on write and login**, so `Priya@College.edu` and `priya@college.edu` are one account.
- **`.test` emails are rejected** by `email-validator` (reserved TLD). Tests use `college.edu`.
- Both profile relationships are always assigned on create (even `None`). Otherwise reading
  `user.student` on a fresh object triggers an async lazy-load error.

**Issues / next**
- Password reset is admin-only (PATCH password). Self-service reset needs an email channel (P1).
