# Transit: Intelligent College Transport Platform

Problem Statement 03 (30-Day Full-Stack, AI & Agentic Product Engineering). This repo holds
the **P0 operations platform** (Team A scope) plus clean extension points for Team B's
intelligence modules and the Transport Operations Agent.

- **Backend:** FastAPI modular monolith, PostgreSQL 16, SQLAlchemy 2 (async), Alembic
- **Frontend:** one Flutter app (web + Android) with student, driver and admin areas
- **Realtime:** WebSocket push for alerts, boarding and the live board

## Quick start
Prerequisites: Docker, Python 3.11+, Flutter 3.35+.

```bash
# 1. Database (Postgres on localhost:5433)
docker compose up -d db

# 2. Backend
cd backend
python -m venv .venv
.venv/Scripts/activate            # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
cp ../.env.example .env
alembic upgrade head
python -m scripts.seed            # St. Joseph's College of Engineering (OMR, Chennai): 3 routes, 4 buses, 3 drivers, 30 students
uvicorn app.main:app --reload     # http://localhost:8000/docs

# 3. Frontend (new terminal)
cd frontend
flutter pub get
flutter run -d chrome
```
Logins, password `transit123` for all: `admin@college.edu` · `driver1@college.edu` (route 14)
· `student1@college.edu` (route 14).

## Day-30 acceptance scenario
With the API running:
```bash
cd backend
python -m scripts.demo_scenario                 # full run, prints each step
python -m scripts.demo_scenario --keep-running  # leave the trip live to watch it in the app
```
Admin assigns students → driver starts trip → student scans the driver's QR → the bus reaches
stop 2 twelve minutes late → the waiting student and admin are notified → the live board shows
the delay → the trip ends, attendance is finalised and the timeline is recorded. The same
scenario runs as an automated test in `backend/app/tests/test_acceptance.py`.

## Live tracking demo
The driver's phone is the bus's GPS: while a trip runs it reports every ~5 s. A stop is marked
arrived when the bus comes within 100 m, and riders of a stop get an alert when it's 2 km away.
Without a phone on a real bus, simulate one:
```bash
cd backend
python -m scripts.simulate_bus                 # drives driver1's route 14 at 10x speed, fix every 2 s
python -m scripts.simulate_bus --direction drop --end
```
Watch it as `student4@college.edu` (waits at Perungudi on route 14) or on the admin **Live map**.
Map tiles default to the public OpenStreetMap server (development only). Before launch, build with
`--dart-define=TILE_URL=<MapTiler/Stadia/self-hosted URL>`.

## Modules (P0)
Each module owns one backend folder and one frontend folder, with its own README (reference)
and DEVLOG (history of decisions).

| Module | What it does | Backend | Suggested owner |
|---|---|---|---|
| auth | users, roles, JWT login | [README](backend/app/modules/auth/README.md) | member 1 |
| master_data | buses, stops, routes, stop order | [README](backend/app/modules/master_data/README.md) | member 1 |
| allocation | student → route + stop, seat guard | [README](backend/app/modules/allocation/README.md) | member 2 |
| trips | schedules, daily trips, start/arrive/end | [README](backend/app/modules/trips/README.md) | member 3 |
| boarding | driver's rotating QR, student check-in, attendance | [README](backend/app/modules/boarding/README.md) | member 4 |
| capacity | occupancy, utilisation, capacity alerts | [README](backend/app/modules/capacity/README.md) | member 4 |
| delay_monitor | schedule-based delay detection + watcher | [README](backend/app/modules/delay_monitor/README.md) | member 5 |
| notifications | who hears what; inbox + WebSocket push | [README](backend/app/modules/notifications/README.md) | member 5 |
| history | event timeline, trip & attendance reports | [README](backend/app/modules/history/README.md) | member 5 |
| dashboard | per-role home aggregates, live board | [README](backend/app/modules/dashboard/README.md) | member 5 |
| tracking (P1) | live bus position, GPS auto-arrival at stops, "bus is 2 km away" alerts | [README](backend/app/modules/tracking/README.md) | Team B |
| reports (P1) | student problem reports, an AI agent (local Ollama model) that checks them against trip records and drafts replies, lost and found | [README](backend/app/modules/reports/README.md) | Team B |

Shared code: [backend/app/core](backend/app/core/README.md) · [frontend](frontend/README.md) ·
[design system](frontend/lib/design/README.md) · [database design](docs/DATABASE.md). How to work in this repo:
[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md).

## How the modules connect
```
             ┌──────────── FastAPI app (backend/app/main.py) ────────────┐
 Flutter ──► │ auth  master_data  trips  allocation  boarding            │
 (REST)      │ capacity  delay_monitor  notifications  history dashboard │
             │        │ services call services (read)                    │
             │        ▼                                                  │
             │  core.events ── publish ──► domain_events table (history) │
             │        └── after commit ──► subscribers in other modules  │
             │  core.realtime (WebSocket /ws) ◄── notifications/dashboard│
             └───────────────────────────────┬───────────────────────────┘
 Flutter ◄── WebSocket push                   ▼
                                     PostgreSQL (one DB)
```
Example chain: driver taps **Arrived** → trips publishes `StopArrived` → delay_monitor sees
+12 min → publishes `TripDelayed` (with affected stops) → notifications picks the students
still waiting downstream and pushes to their phones; dashboard pushes a refresh to the board.

## Tests
```bash
cd backend && .venv/Scripts/python -m pytest -q      # 148 API/service tests incl. acceptance
cd frontend && flutter analyze && flutter test
```
Backend tests use the `transit_test` database created by `docker compose`.

## For Team B (P1/P2 + agent)
- Subscribe to `TripDelayed`, `OverCapacity`, `StudentBoarded`… (see module READMEs) in your module's `register()`.
- Read history from the `domain_events` table / `GET /history/events`.
- Call module services as agent tools: `allocation.service.assign`, `trips.service.*`,
  `notifications.service.messages_for/deliver`, `capacity.service.route_utilization`.
- GPS: every position source goes through `tracking.service.ingest()` (driver's phone today; Traccar
  or hardware later). For ETA-based alerts, feed `delay_monitor.service.evaluate` with `source=gps`.
- Push notifications (FCM): extend `notifications.service.deliver()`. `BusApproaching` and all other
  alerts then reach phones with the app closed.

## Deploying
[docs/DEPLOY.md](docs/DEPLOY.md): one VM with Docker (Postgres, the API as **one** process, Caddy for
HTTPS and the web app), backups, updates and the Android release build. CI runs the backend tests,
`alembic check`, `flutter analyze`/`test` and the API image build on every push
([.github/workflows/ci.yml](.github/workflows/ci.yml)).

## Ports
| Service | Port |
|---|---|
| Postgres (this project) | 5433 (5432 was already used on the dev machine) |
| API | 8000 |
| pgAdmin (`docker compose --profile tools up`) | 5050 |
