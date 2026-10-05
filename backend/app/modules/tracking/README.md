# tracking: live bus position, auto-arrival and "bus is near" alerts

> Where each bus is right now. Checks the bus in at stops by GPS and tells riders when it's 2 km away.

| | |
|---|---|
| **Owner** | Team B |
| **Backend** | `backend/app/modules/tracking/` |
| **Frontend** | `frontend/lib/modules/tracking/` |
| **Status** | P1: driver's phone as the GPS source |

## How it works
```
driver's phone ──POST /trips/{id}/positions (batch)──► tracking.service.ingest()
   (every ~5 s, buffered offline)                        │ store every fix in bus_positions
                                                         │ fix ≤ 100 m from one of the next 2 unreached stops
                                                         │   └► trips.service.arrive_at_stop(observed_at=fix time)
                                                         │        └► StopArrived {source: "gps"} → delay_monitor,
                                                         │           notifications, dashboard, history (unchanged)
                                                         │ fix ≤ 2 km from an unreached stop, first time this trip
                                                         │   └► BusApproaching → notifications picks the riders
                                                         ▼
                                  after commit: WebSocket "position" → route:{id} topic + admins
```
- **Every source goes through `ingest()`.** A Traccar bridge or a hardware feed later only needs
  to call it; the rules stay in one place.
- **Fixes with `accuracy_m` above `MAX_FIX_ACCURACY_M` (100)** are stored but never trigger arrival
  or alerts.
- **Look-ahead of 2 stops.** Auto-arrival considers only the next `ARRIVAL_LOOKAHEAD_STOPS`
  unreached stops. A missed stop is skipped when the bus reaches the one after it (as with the
  manual button), but a road that passes near a much later stop can't skip half the route.
- **Time of arrival** is the fix's `recorded_at` (clamped between trip start and now), so fixes
  buffered during a network drop still record the right arrival time. Future timestamps are
  treated as now.
- **The driver's ARRIVED button stays.** It covers stops without coordinates and GPS trouble.
- **One ingest per trip at a time.** The trip row is locked for the request, so two batches
  can't check the bus in twice.
- **Distance is straight-line (haversine).** It's cheap and needs no PostGIS or routing
  server. On a winding road the 2 km alert comes a little early; for a heads-up that's fine.
  A stop whose straight-line distance is short but whose road distance is long (across a
  river, say) can be alerted early.

## Who hears `BusApproaching`
Handled by notifications (`approach_student_targets`), once per trip and stop:

| Direction | Recipients |
|---|---|
| pickup | students allocated to that stop who have **not boarded** |
| drop | students **on board** (scanned the QR) whose stop it is |

The campus stop has no allocated riders, so its alert reaches nobody. It's still recorded.

## Data model
| Table | Key columns |
|---|---|
| `bus_positions` | `trip_id`, `bus_id`, `latitude`, `longitude`, `speed_kmph`, `heading_deg`, `accuracy_m`, `recorded_at`; index (`trip_id`, `recorded_at`) |
| `approach_alerts` | `trip_id`, `stop_id`, `sequence`, `distance_m`, `created_at`; unique (`trip_id`, `stop_id`) |

Stop coordinates live in master_data's `stops.latitude/longitude`. A stop without them is
drawn nowhere and never auto-arrives.

## API
| Method | Path | Role | Purpose |
|---|---|---|---|
| POST | `/trips/{id}/positions` `{positions: [{latitude, longitude, speed_kmph?, heading_deg?, accuracy_m?, recorded_at?}]}` (1–500) | the trip's driver; admin when `ALLOW_SIMULATION` | ingest; returns `{accepted, arrived[], approaching[], position}` |
| GET | `/trips/{id}/live` | admin; the trip's driver; students allocated to its route | route, stops with coordinates, next stop, latest position |
| GET | `/tracking/live` | admin | the same for every running trip |

Errors: 403 not your trip, 422 `bad_trip_state` (trip not running: the phone stops reporting).

WebSocket: `{"type": "position", "data": {trip_id, route_id, bus_id, latitude, longitude, speed_kmph, heading_deg, accuracy_m, recorded_at}}`
to `route:{route_id}` subscribers and to every admin socket. Only the newest fix of each batch is pushed.

## Settings
| Env | Default | |
|---|---|---|
| `ARRIVAL_RADIUS_M` | 100 | auto-arrival radius |
| `APPROACH_RADIUS_M` | 2000 | "bus is near" radius |
| `MAX_FIX_ACCURACY_M` | 100 | worse fixes never trigger anything |
| `ARRIVAL_LOOKAHEAD_STOPS` | 2 | how many unreached stops auto-arrival considers |
| `POSITION_RETENTION_DAYS` | 90 | fixes older than this are deleted (job `position-retention`, every 6 h) |

## Events
| Emits | Payload |
|---|---|
| `BusApproaching` | `trip_id, route_id, bus_id, driver_id, direction, sequence, stop_id, stop_name, distance_m, scheduled_at, expected_at, recorded_at` |

`StopArrived` (emitted by trips) now carries `source: "gps" \| "manual"`.

## Public service API
`ingest(session, trip_id, fixes, principal)`, `broadcast(position, trip)`, `latest_positions(trip_ids)`,
`live_trip(trip_id)`, `live_trips(trips)`.

## Frontend
| Piece | Role | File |
|---|---|---|
| Position reporter (GPS stream → buffered batches; Android foreground service) | driver | `frontend/lib/modules/tracking/state/position_reporter.dart` |
| Location sharing strip | driver run screen | `frontend/lib/modules/tracking/widgets/sharing_strip.dart` |
| Live map (route line, stops, bus, "You", distance) | driver, student, admin | `frontend/lib/modules/tracking/widgets/live_map.dart` |
| Live map screen (all running buses) | admin | `frontend/lib/modules/tracking/screens/admin_live_map_screen.dart` |

Map tiles come from `--dart-define=TILE_URL=...` (default: the OpenStreetMap public server,
**development only**: switch to MapTiler/Stadia/self-hosted before launch; see `frontend/lib/core/config.dart`).

## How to test
```bash
cd backend && .venv/Scripts/python -m pytest app/modules/tracking -q
# with the API running and seeded data:
python -m scripts.simulate_bus                          # drives driver1's route at 10x speed
python -m scripts.simulate_bus --direction drop --end
```

## Next
- **FCM push** so alerts reach phones with the app closed: extend `notifications.deliver()`.
  Recipients and wording already come from `messages_for`.
- **Traccar / hardware trackers:** a small bridge that reads Traccar's WebSocket and calls `ingest()`.
- **ETA from speed and position** instead of timetable + delay, fed to `delay_monitor.evaluate(source=gps)`.
- **Retention:** prune `bus_positions` older than N days (~720 rows per bus per hour).
