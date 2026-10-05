# Running and testing live tracking

How to run the platform locally and see live bus tracking working: the moving bus on the map,
stops marked arrived automatically, and the "bus is 2 km away" alert. Section 5 covers student
reports and the AI agent that checks them.

You'll need three terminals: the API, the app and a bus simulator. Postgres must be running first.
To check everything works without the app, you only need the API and the simulator: see
[Check the whole flow end to end](#check-the-whole-flow-end-to-end).

**Prerequisites:** Docker Desktop, Python 3.11+, Flutter 3.35+. The first-time setup in
[README.md](README.md#quick-start) must be done: `backend/.venv` created with
`pip install -e ".[dev]"`, and `backend/.env` copied from `.env.example`.

## 1. One-time setup
```powershell
# Start Postgres (Docker Desktop must be running)
docker compose up -d db

cd backend
.venv\Scripts\activate
alembic upgrade head
python -m scripts.seed --reset     # WIPES the database, then loads the St. Joseph's demo data
```
> **`--reset` deletes everything in the dev database.** You need it if your database was seeded
> before live tracking was added: the old demo stops have no coordinates, so the map and
> automatic arrival won't work for them. On a fresh database, `python -m scripts.seed` is enough.

## 2. Terminal 1: API
```powershell
cd backend
.venv\Scripts\activate
uvicorn app.main:app --reload
```
Check it at http://localhost:8000/health. The API docs are at http://localhost:8000/docs.

## 3. Terminal 2: the app
```powershell
cd frontend
flutter pub get
flutter run -d chrome
```
Log in as `student4@college.edu`. The password for every demo account is `transit123`. This
student waits at Perungudi on route 14.

## 4. Terminal 3: run the bus simulation
The simulator stands in for a driver's phone and the students on the bus. It needs the API from
step 2 running and seeded data from step 1.

```powershell
cd backend
.venv\Scripts\activate
python -m scripts.simulate_bus --speedup 5 --interval 2 --end
```
This signs in as driver1, starts a route 14 pickup trip departing now, and drives it along the
stops at 10x speed, sending a GPS position every 2 seconds. As it goes:
- the API marks each stop arrived when the bus gets within 100 m of it;
- students waiting at the next stops get a "bus is 2 km away" alert;
- students board by scanning the driver's QR as the bus reaches their stop. About 3 in 4 of
  those allocated turn up; the rest are absent.

The terminal prints a line for each position, plus each alert, arrival and boarding:
```
Started one-off pickup trip #7 on route 14 (bus TN09AB1401).
9 students will board.
    boarded Aarav V (1 on board)
Driving 6 stops at 30 km/h, 10x speed. Ctrl+C to stop.
Tidel Park -> Perungudi (3.2 km)
  12.97802,80.24556    30 km/h  2 km alert Perungudi
  12.96110,80.24130     0 km/h  ARRIVED Perungudi
```
Without `--end` the trip stays running at the last stop, so you can look around in the app.

> `scripts.demo_scenario` and `scripts.simulate_reports` send made-up arrival times as the admin,
> which needs `ALLOW_SIMULATION=true` in `backend/.env` (it's in `.env.example`; production keeps it
> off). Without it they stop with `403 ... simulation_off`.
End it from the driver app, or run the simulator with `--end` next time.

### What you should see as student4
- The live map appears on the home screen with the bus moving along the route.
- A "Route 14 bus is 1.9 km from your stop" alert pops up.
- Perungudi turns grey on the line once the bus reaches it.

### Other views
| Log in as | What to look at |
|---|---|
| `admin@college.edu` | **Live map** in the side menu: every running bus, with a list to follow one |
| `driver1@college.edu` | The run screen: the map, the location-sharing strip, and stops checking in automatically |

Use a second browser window (or a private window) to be signed in as two users at once.

### Check the whole flow end to end
To confirm tracking, alerts, boarding and attendance all work without opening the app, drive
each route quickly and end the trip. Only the API (step 2) needs to be running:
```powershell
cd backend
.venv\Scripts\activate
python -m scripts.simulate_bus --speedup 120 --interval 0.3 --end
python -m scripts.simulate_bus --driver driver2@college.edu --direction drop --speedup 120 --interval 0.3 --end
python -m scripts.simulate_bus --driver driver3@college.edu --speedup 120 --interval 0.3 --end
```
Each run takes under a minute. A run passed if it ends like this:
```
Trip #7 ended.
Attendance: 9 present, 0 absent.
✓ Attendance matches who boarded.
```
Things to check in the output:
- every stop on the route has an `ARRIVED` line;
- `boarded` lines appear right after the arrival at that student's stop (on a drop run, all of
  them come before the bus leaves campus);
- the last line is `✓ Attendance matches who boarded`.

If the attendance written for the trip doesn't match who boarded, the simulator prints the
student ids that differ and exits with an error (exit code 1). Any failed API call prints a line
starting with `✗` and also exits with an error.

### Simulator options
| Option | Effect |
|---|---|
| `--end` | End the trip at the last stop, then check attendance |
| `--driver driver2@college.edu` | Route 7 (ECR). Use `driver3@college.edu` for route 22 (Tambaram). Default: driver1, route 14 |
| `--direction drop` | Evening drop run, campus to the stops. Default: `pickup` |
| `--board-rate 1` | Share of allocated students who board (default 0.75). `0` means nobody boards |
| `--speedup 120 --interval 0.3` | Very fast: for checks, not for watching in the app |
| `--speedup 1 --interval 5` | Realistic speed, one position every 5 seconds |
| `--speed 40` | Bus speed in km/h (default 30) |
| `--dwell 2` | Positions sent while standing at each stop (default 2) |
| `--noise 6` | GPS jitter in metres (default 6) |
| `--api http://localhost:8000` | Where the API is running (default shown) |

Run `python -m scripts.simulate_bus --help` for the full list.

### Good to know
- **Which trip it drives:** if the driver already has a trip running today, the simulator carries
  on with it. Otherwise it creates a one-off trip departing now on the driver's usual route
  and bus, so you don't get "started 7 hours late" alerts.
- **The same students board every time** for a given trip number, so reruns are comparable.
- **Drop runs:** a student only gets the "your stop is coming up" alert if they've scanned the
  boarding QR on that trip. Otherwise the app can't tell they're on the bus. The simulator
  boards drop riders at campus, so these alerts fire.
- **Ctrl+C** stops the bus but leaves the trip running. Running the simulator again the same day
  carries on with that trip. If it's never ended, it closes itself the next day (once it has
  been inactive for 3 hours), so a forgotten trip never stops the driver starting new ones.
- **It writes to your dev database.** Each run leaves a completed trip, its GPS positions,
  boardings, attendance and notifications. To start clean, run
  `python -m scripts.seed --reset` (this wipes the dev database).

## 5. Student reports and the AI agent
Students report problems (late or skipped stop, overcrowding, safety, lost item). An agent checks
each report against the trip records, sets its urgency and drafts a reply. Admins review it on
the **Issues** screen. The agent runs on a **local model through Ollama**, so nothing leaves your
laptop.

### One-time setup
1. Install Ollama from https://ollama.com and start it (it runs in the system tray).
2. Download the two models (about 3 GB):
   ```powershell
   ollama pull qwen3:4b
   ollama pull nomic-embed-text
   ```
3. Add the settings from `.env.example` (the `REPORT_AI` and `OLLAMA_*` lines) to `backend/.env`,
   or leave them out to use the defaults.

Without Ollama everything still works: reports are checked with keyword rules and templates, and
the admin screen says "Written by the rule-based checker". Set `REPORT_AI=rules` to force that.

### Check it end to end (no app needed)
With the API running (step 2):
```powershell
cd backend
.venv\Scripts\activate
python -m scripts.simulate_reports
```
This starts a one-off route 14 trip with known facts:
- the bus reaches stop 2 twelve minutes late;
- it skips stop 3;
- GPS shows speeds up to 88 km/h;
- the driver logs a water bottle as found.

Then six students file reports and the script checks each verdict. It passed if it ends with:
```
✓ All 6 reports got the expected verdict.
```
Each report takes about 6 seconds with the model, except the first, which can take up to a minute
while the model loads. The script refuses to run if driver1 already has a trip running.

### Try it in the browser
1. As `student4@college.edu`, go to **Reports**, then **New** (or **Report a problem** on My line,
   or a trip on **Trips**). Pick a kind and a trip, describe the problem, and send it.
2. As `admin@college.edu`, open **Issues** in the side menu. Within a few seconds the report shows
   the agent's summary. Open it to see:
   - each finding, marked Confirmed, Partly, Not supported or No data;
   - the suggested action;
   - the draft reply, which you can edit and send.
3. Back as student4, the reply arrives as an alert and in the report's thread.

Drivers log found items with **Found item** on the run screen. Lost-item reports then list
likely matches; **Match** tells the student to collect the item.

## 6. Automated tests
```powershell
cd backend
.venv\Scripts\python -m pytest -q                        # all 148 backend tests
.venv\Scripts\python -m pytest app/modules/tracking -q   # just the tracking tests

cd ..\frontend
flutter analyze
flutter test
```
The backend tests use the `transit_test` database, which `docker compose` creates, so they never
touch your dev data.

## 7. Real GPS on a phone (optional)
Browsers only share location on HTTPS or `localhost`, so the driver needs the Android app.

1. Put the phone on the same Wi-Fi as your laptop.
2. Start the API so the phone can reach it:
   ```powershell
   uvicorn app.main:app --host 0.0.0.0
   ```
   Allow Python through Windows Firewall if asked.
3. Plug the phone in (with USB debugging on) and run:
   ```powershell
   cd frontend
   flutter run --dart-define=API_BASE=http://<laptop-ip>:8000
   ```
4. Log in as `driver1@college.edu`, start a trip, and allow location when asked. The run screen
   shows **Sharing**, and the phone shows a "Sharing bus location" notification.

To have stops arrive automatically where you actually are, add a stop with your current
coordinates: **Network → pick a route → Add stop**, then fill in the location as
`latitude, longitude` (copy it from a map app). The demo stops are on OMR, so they won't match
your location.

## Troubleshooting
| Problem | Fix |
|---|---|
| `docker compose up` fails to connect | Start Docker Desktop and wait until it says it's running |
| Seed says "The database is at migration …", or anything fails with `column … does not exist` | The code has a newer migration than your database (after a `git pull`): run `alembic upgrade head` in `backend/`, then restart the API |
| Map says "No map for this route yet" | The stops have no coordinates: re-seed with `--reset` (see step 1) |
| Simulator exits with "Can't sign in" | The API isn't running, or the database isn't seeded: run `python -m scripts.seed` |
| Simulator says "This route's stops have no coordinates" | The database was seeded before live tracking: re-seed with `--reset` (see step 1) |
| Simulator exits with `409 ... already_running` | Another trip for this driver or bus is still running today: log in as that driver and end it from the run screen, or as admin call `POST /trips/{id}/end` from http://localhost:8000/docs |
| Student sees no alert | Check you're logged in as a student on the simulated route, waiting at a stop after the first one, and not boarded |
| Driver strip shows **Blocked** or **Location off** | Tap the button on the strip to open the phone's settings |
| Reports stay "Checking the bus records…" | The model is loading (up to a minute the first time). If it never finishes, check Ollama is running: http://localhost:11434 should say "Ollama is running" |
| Admin screen says "rule-based checker" | Ollama wasn't reachable, or `REPORT_AI=rules`. Start Ollama and press **Check again** on the report |
| Map tiles don't load | The public OpenStreetMap server may be rate-limiting; set `--dart-define=TILE_URL=...` to another provider |
