# reports: student problem reports, the triage agent, lost and found

> Students report problems with their bus; an agent checks each report against the trip records
> and drafts a reply; the transport office decides what to do.

| | |
|---|---|
| **Owner** | Team B |
| **Backend** | `backend/app/modules/reports/` |
| **Frontend** | `frontend/lib/modules/reports/` |
| **Status** | done (first build: no clustering, no photos, English only) |

## Responsibilities
- Students file a report: what kind (late or skipped stop, overcrowding, safety or conduct, lost
  item, something else), which of their recent trips, their own words, and optionally their name
  hidden from staff.
- The **agent** analyses every report in the background (it never slows the student's request):
  1. **Read:** a local model (Ollama, `qwen3:4b`) pulls out the claim: subtype, minutes late,
     the stop mentioned, and any extra checks worth running. Keyword rules run as well and win
     for phrases they can't misread ("drove past", "never came", "touching", "accident").
  2. **Evidence (code only):** each check reads the system's own records and gives a verdict:
     `confirmed`, `partly`, `not_supported` or `no_data`.
  3. **Urgency:** rules set the minimum. A safety report or harassment/accident words make it
     `critical`; confirmed speeding or a bus that never ran make it at least `high`. The model can
     raise urgency up to `high`, never lower it, and never set `critical` itself.
  4. **Write:** the model drafts an admin summary, a suggested action and a reply to the student,
     using only the findings (no driver name, no other students).
- Admins reply (editing the draft), match lost items to found items, re-run the check, and close.
  **The agent never sends, matches or closes anything itself.**
- Drivers and admins log found items; the agent suggests matches for lost-item reports.

It does **not** decide anything about a person. Conduct reports get a `conduct: no_data` finding
("needs a staff follow-up"): trip data can't show behaviour.

### Evidence checks
| Check | Source | Confirmed when |
|---|---|---|
| `on_this_trip` | boarding, allocation | the student boarded (partly: allocated to the route but didn't scan) |
| `lateness` | `trip.stop_events` at the student's stop (or the stop they name) | delay ≥ `DELAY_THRESHOLD_MIN` and within ±25% (min 3) of the minutes claimed; otherwise partly or not supported |
| `stop_reached` | `trip.stop_events` | no arrival at their stop, but a later stop was reached or the trip ended |
| `trip_ran` | trip status | cancelled, or never started 5+ min after departure |
| `crowding` | `capacity.trip_occupancy` | boarded ≥ capacity (partly at ≥ `CAPACITY_WARN_PCT`) |
| `speed` | `bus_positions` (read-only) | 2+ GPS readings over `REPORT_SPEED_LIMIT_KMPH` (60) |
| `conduct` | none | always `no_data`: staff follow-up |
| `lost_item` | `found_items` on the same trip or bus, last `REPORT_LOOKBACK_DAYS` | candidates ranked by embedding similarity (`nomic-embed-text`), or word overlap when the model is off |

### When the model isn't there
Ollama not running, a timeout, or output that doesn't fit the schema: that step falls back to
keyword rules and templates. `analysed_by` says `rules`, or `qwen3:4b+rules` when only one step
used the model. The model's output is capped (`num_predict`) so a runaway answer fails fast.

### Numbers in the draft are checked
Every number and time in the model's summary, suggested action and draft reply must appear in
the report text or the findings. If one doesn't (the model once wrote "6:00 km/h" for 60 km/h),
the rule-based summary and draft are used instead. `REPORT_AI=rules` forces this (tests do).
A sweeper job retries any report still `pending` after a restart.

### Prompt injection
The student's text goes to the model as quoted data with an instruction never to follow it. The
model's output is constrained to a JSON schema with no field that can change anything, and its
only effects are a summary, a draft and extra *checks* (which only read data).

## Anonymous reports
The admin view hides the reporter's name, roll no **and allocated stop**, and the analysis never
contains anything that could be matched against a roster: the boarding check says "The reporter
boarded this trip" with no time, and stop checks use only a stop the student named.

## Guard rails on the text
- Urgent (critical) words are matched as whole words and phrases (`llm.CRITICAL_PATTERNS`):
  "keep in touch" or "the app crashed" don't page every admin; "touched me" or "crashed into" do.
- Delays are read in minutes only ("800m" is a distance) and capped at 600 min.
- A model draft reply that mentions the bus staff, makes promises or claims causes
  (`llm.reply_breaks_rules`) is replaced by the template, like one quoting numbers not in the records.

## Data model
| Table | Key columns |
|---|---|
| `reports` | `student_id`, `trip_id`, `route_id`, `stop_id`, `kind`, `description`, `anonymous`, `status` (open/replied/closed), `severity`, `analysis_status`, `analysis` (jsonb), `analysed_by` (`qwen3:4b`, `rules` or `qwen3:4b+rules`), `analysed_at`, `matched_found_item_id`, `closed_at`, `closed_by`, `resolution_note` |
| `report_messages` | `report_id`, `author_id`, `from_staff`, `body`, `created_at` |
| `found_items` | `trip_id`, `bus_id`, `logged_by`, `description`, `status` (unclaimed/matched/returned), `embedding` (float[]) |

## API
| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/reports/trip-options` | student | their trips from the last 3 days (boarded, or on their route), for the picker |
| POST | `/reports` | student | file a report `{kind, description, trip_id?, anonymous}`. 429 `rate_limited` after `REPORTS_PER_HOUR` (5) |
| GET | `/reports/mine` | student | their reports |
| GET | `/reports/{id}` | student (own), admin | student view has no analysis/severity; admin view hides name and roll no when anonymous |
| POST | `/reports/{id}/messages` | student (own) | follow-up; reopens a replied report |
| GET | `/reports?status&kind&severity` | admin | inbox: open critical/high first, then newest |
| POST | `/reports/{id}/reply` | admin | send a reply (status → replied) |
| POST | `/reports/{id}/close` | admin | `{note?}` |
| POST | `/reports/{id}/reanalyse` | admin | run the agent again |
| POST | `/reports/{id}/match/{found_item_id}` | admin | lost-item reports only; re-matching puts the previous item back to `unclaimed` |
| POST | `/found-items` | driver (own trip, running or ended today), admin | `{trip_id?, description}` |
| GET | `/found-items?status` | driver (own), admin | |
| PATCH | `/found-items/{id}` | admin | `{status}` e.g. returned |

## Events
**Emits** (payloads never include the student's id; anonymous reports have no `actor_id`)

| Event | When | Payload keys |
|---|---|---|
| `ReportSubmitted` | student files a report | `report_id, kind, trip_id?` |
| `ReportAnalysisRequested` | admin asks for a re-check | `report_id` |
| `ReportAnalysed` | agent finished | `report_id, kind, severity, first, summary, trip_id?` |
| `ReportFollowUp` | student adds a message | `report_id, kind` |
| `ReportReplied` | admin replies | `report_id, kind, body` |
| `ReportClosed` | admin closes | `report_id, kind, note` |
| `FoundItemLogged` | driver/admin logs an item | `found_item_id, description` |
| `LostItemMatched` | admin matches | `report_id, found_item_id, description` |

Notifications turns `ReportAnalysed` (first analysis only) and `ReportFollowUp` into admin alerts,
and `ReportReplied`, `ReportClosed`, `LostItemMatched` into student alerts. The module also pushes
an `ops` message to admins for every event above so the Issues screen refreshes. It deliberately
doesn't use the dashboard's forwarding, which also reaches students on the route.

**Consumes**

| Event | Why |
|---|---|
| `ReportSubmitted`, `ReportAnalysisRequested` | run the agent |
| `FoundItemLogged` | store the item's embedding for matching |

## Public service API (for other modules)
`get_report(session, id)`, `recipient_id(session, report_id)` (who to notify; used by notifications).

## Settings
| Env var | Default | |
|---|---|---|
| `REPORT_AI` | `ollama` | `rules` skips the model |
| `OLLAMA_URL` | `http://localhost:11434` | |
| `OLLAMA_MODEL` | `qwen3:4b` | any Ollama chat model that supports structured output |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | lost-and-found matching |
| `OLLAMA_TIMEOUT_SECONDS` | `120` | the first call after a while also loads the model |
| `REPORT_SPEED_LIMIT_KMPH` | `60` | |
| `REPORT_LOOKBACK_DAYS` | `3` | |

## Frontend screens
| Screen | Role | File |
|---|---|---|
| Report a problem (`/student/reports/new`, `?trip=` to preselect) | student | `screens/report_form_screen.dart` |
| My reports and a report's thread (`/student/reports`) | student | `screens/my_reports_screen.dart` |
| Issues inbox and Found items tab (`/admin/issues`) | admin | `screens/admin_issues_screen.dart` |
| Issue detail: evidence, draft reply, match, close (`/admin/issues/:id`) | admin | `screens/admin_issue_screen.dart` |
| Found item sheet (run screen) | driver | `widgets/log_found_item_sheet.dart` |

## How to test
```bash
cd backend && .venv/Scripts/python -m pytest app/modules/reports -q   # rules mode + a fake Ollama
python -m scripts.simulate_reports                                     # real model, API running
```
`simulate_reports` builds a trip with known facts (12 min late at stop 2, stop 3 skipped, GPS at
85 km/h, a water bottle found), files six reports and checks every verdict.

## Extension notes
- **Clustering and patterns** (next step): group open reports by route, kind and day; a weekly
  job can summarise recurring problems from `reports` + `delay_reports` + attendance.
- **Photos:** add an upload endpoint and a vision model (e.g. `qwen2.5vl:3b`, already installed).
- **Tamil:** switch `OLLAMA_MODEL` to a larger model and add the language to the write prompt.
