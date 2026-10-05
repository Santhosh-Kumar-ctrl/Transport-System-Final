# auth: users, roles, login

> Who is using the system and what they're allowed to do.

| | |
|---|---|
| **Owner** | Team A, member 1 |
| **Backend** | `backend/app/modules/auth/` |
| **Frontend** | `frontend/lib/modules/auth/` |
| **Status** | P0 done |

## Responsibilities
- User accounts with one role each: `student`, `driver`, `admin`.
- Student profile (roll no, department, year) and driver profile (licence).
- JWT login: short-lived access token (`ACCESS_TOKEN_MINUTES`) + refresh token (`REFRESH_TOKEN_DAYS`).
- Admin-only user management. There's no self sign-up: the transport office creates accounts.

Not here: route/stop assignment (allocation), driver-to-bus assignment (trips schedules).

## Data model
| Table | Key columns |
|---|---|
| `users` | `email` (unique, lower-cased), `password_hash` (bcrypt), `full_name`, `phone`, `role`, `is_active` |
| `student_profiles` | `user_id` PK/FK, `roll_no` (unique), `department`, `year` |
| `driver_profiles` | `user_id` PK/FK, `license_no` (unique), `license_expiry` |

## API
| Method | Path | Role | Purpose |
|---|---|---|---|
| POST | `/auth/login` | public | email + password → `{access_token, refresh_token, user}`. 429 `rate_limited` after `LOGIN_ATTEMPTS_PER_MINUTE` (10) tries per address + email |
| POST | `/auth/refresh` | public | refresh token → new pair (401 `inactive` if deactivated, `session_revoked` after a password change) |
| GET | `/auth/me` | any | current user with profile |
| GET | `/users?role=&q=&active=` | admin | list/search (q matches name, email, roll no) |
| POST | `/users` | admin | create; `student` profile required for role=student, `driver` for role=driver |
| GET/PATCH | `/users/{id}` | admin | view / edit / (de)activate / reset password. 409 `profile_taken` for a duplicate roll/licence no; 422 `last_admin` when deactivating the last active admin |

Error codes: `bad_credentials`, `inactive`, `email_taken`, `roll_no_taken`, `license_taken`, `wrong_role`.

## Events
| Emits | When |
|---|---|
| `UserCreated` | account created |
| `UserActivated` / `UserDeactivated` | `is_active` toggled |

## Public service API
`get_user`, `get_users(ids)`, `briefs(ids)`, `brief(user)`, `ensure_role(id, role)`,
`admin_ids()`, `find_student_by_roll_no(roll_no)`.

## Security notes
- Tokens carry `typ` (`access` / `refresh` / `board`), so one kind can't be used as another.
- `Principal` is decoded from the token without a DB lookup. A deactivated user keeps REST access
  until their access token expires (≤30 min); refresh and WebSocket connects are refused at once.
- **Session revocation:** every token carries the user's `token_version` (`ver`). Changing the
  password or deactivating the account bumps it, which ends every session at its next refresh,
  and publishes `UserSessionsRevoked`, which closes the user's open sockets right away.
- **Password hashing runs in a worker thread** (`security.hash_password_async` /
  `verify_password_async`): bcrypt takes ~250 ms of CPU, which on the event loop would stall every
  other request during a wave of logins. Unknown emails are checked against a dummy hash, so
  login time doesn't reveal which accounts exist.
- **Login rate limit** per IP + email (not per IP alone, so a campus behind one NAT address isn't
  locked out).
- `BCRYPT_ROUNDS` defaults to 12 (tests use 4 for speed).

## Frontend screens
| Screen | Role | File |
|---|---|---|
| Login | all | `frontend/lib/modules/auth/screens/login_screen.dart` |
| People (list/create users) | admin | `frontend/lib/modules/auth/screens/admin_people_screen.dart` |

## How to test
```bash
cd backend && .venv/Scripts/python -m pytest app/modules/auth -q
```

## Extension notes
- SSO/OTP can replace `/auth/login` without touching other modules (they only see `Principal`).
