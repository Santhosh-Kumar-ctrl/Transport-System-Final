# core: shared infrastructure

Not a business module. Everything every module needs, and nothing module-specific.

| File | What it gives you |
|---|---|
| `config.py` | `settings` (env vars / `backend/.env`): DB URLs, JWT, thresholds, timezone. `ENVIRONMENT=production` refuses to start with unsafe values (`production_problems()`) |
| `db.py` | async `engine`, `SessionLocal`, `get_session` FastAPI dependency, `flush_or_conflict()` (unique/FK violation → 409) |
| `models.py` | `Base` (with constraint naming convention), `TimestampMixin`, `str_enum()` |
| `roles.py` | `Role` enum: student, driver, admin |
| `security.py` | bcrypt hashing (`*_async` versions run in a thread: use them in request code), `sign()/verify()` JWTs with a `typ` claim, access/refresh tokens with a `ver` (token version) claim |
| `deps.py` | `current_principal`, `require_roles(...)` → `Principal(id, role, ver, expires_at)` decoded from the token (no DB hit) |
| `errors.py` | `NotFound`, `Conflict`, `Forbidden`, `Unauthorized`, `InvalidState`, `TooManyRequests` → JSON `{detail, code}` |
| `events.py` | Domain event bus + `domain_events` table (see below) |
| `realtime.py` | WebSocket hub at `/ws` (user / role / topic channels, topic access policies) |
| `ratelimit.py` | `RateLimiter(name, limit=, window_seconds=).hit(key)` → 429 `rate_limited` (in-memory, per process) |
| `schemas.py` | `PatchModel`: partial-update bodies whose `NOT_NULL` fields reject an explicit `null` (422) |
| `tasks.py` | `tasks.every(seconds, name, fn)` periodic jobs, started by the app lifespan |
| `timeutil.py` | `now_utc()`, `today_local()`, `local_to_utc(date, time)`, `minutes_between()` |

## Event bus contract
```python
await events.publish(session, "TripStarted", {...}, aggregate=("trip", trip.id), actor_id=p.id)
await session.commit()   # row written to domain_events; subscribers run AFTER commit
```
- The payload is normalised to JSON (datetimes → ISO strings) **before** subscribers see it,
  so handlers read exactly what history reads.
- Rolled-back transactions dispatch nothing.
- Each handler runs in its own task and opens its own `SessionLocal()`. A failing handler is
  logged and never affects the publisher or other handlers.
- `await events.drain()` waits for all handler cascades (used by tests).

## WebSocket protocol
```
connect   ws://host:8000/ws
send      {"action":"auth","token":"<access token>"}       first frame, within 5 s
receive   {"type":"authenticated","data":{}}
send      {"action":"subscribe","topic":"route:3"}   {"action":"ping"}
receive   {"type":"notification"|"ops"|"boarding"|"position"|"pong", "data":{...}}
          {"type":"error","data":{"topic":"route:9","code":"forbidden"}}   subscription refused
close     4401 = token missing, invalid, expired or revoked: refresh the token and reconnect
```
- The token goes in a frame, never the URL, so it can't end up in access logs.
- **Topics are access-controlled.** A module that owns a topic prefix registers who may follow it
  with `realtime.set_topic_policy(prefix, fn)` in its `register()`. Today: `route` (allocation) and
  `trip` (trips). Admins may follow anything; a prefix without a policy is refused. Everything sent
  to a topic is seen by every follower, so never put another student's identity in a topic message.
- A socket is closed when its access token expires, and at once when the user's sessions are
  revoked (`hub.disconnect_user`).

In-memory, single process. To scale to several workers, back `Hub` with Redis pub/sub
(see [docs/DEPLOY.md](../../../docs/DEPLOY.md)).

## Event delivery
Events are dispatched in-process after commit and are **not** replayed after a crash or restart.
Anything that must happen for every event needs its own safety net: attendance has the
`attendance-reconciler` job, report analysis has the `report-analyser` retry loop.

## Timezone
Schedules are college-local wall-clock times (`TIMEZONE`, default `Asia/Kolkata`).
All timestamps are stored as UTC `timestamptz`.
