# reports: dev log

## 2026-10-04: Review fixes (Review.md)
**Fixed**
- Anonymous reports could be traced back: the analysis gave the exact boarding time, which the
  trip roster maps to one student (B4). Now there is no time and no allocated stop.
- "Driver stopped 800m away" failed the whole analysis (800 read as minutes > 600) (H4).
- "Please keep in touch" made a lateness report CRITICAL (the "touch" substring) (H4).
- Model drafts guessed causes, promised outcomes or named the driver. A guard now rejects them, and
  the prompt gives a fixed closing sentence instead of quoting what not to say (quoting it primed
  the model to use it).
- Report rate limit (M3); re-matching frees the first item (L1); the retry loop skips reports
  already being analysed (L2); `GET /reports` loads lookups once for the whole list (H3).

## 2026-10-02: Guarding the model's drafts
**Changed**
- Every number and time in the model's write-up must appear in the report or the findings;
  otherwise the rule-based summary and draft are used.
- The model's output is capped (200 tokens to read, 600 to write).
- `analysed_by` is `qwen3:4b+rules` when only one step used the model.

**Why**
- A full `simulate_reports` run produced a draft saying "above 6:00 km/h" (the finding said 60)
  and another with a mangled stop name. Admins edit drafts, but a wrong figure in a draft
  undermines the point of checking facts in code.
- One write-up ran for the whole 120 s timeout while the others took about 6 s: a small model
  can get stuck repeating itself. With the cap it stops within seconds and falls back.
- The timed-out report showed `analysed_by = qwen3:4b` though its draft came from the rules.

## 2026-10-02: First build: reports, triage agent, lost and found
**Built**
- `reports`, `report_messages`, `found_items` tables (migration `bbb63096dc6b`).
- Student report form with a recent-trip picker and "hide my name from staff"; My reports with
  the conversation; admin Issues inbox and detail; driver "Found item" sheet.
- The agent: read (local model) → evidence (code) → urgency (rules floor) → write (local model),
  run after commit from the `ReportSubmitted` subscriber, one at a time, with a retry sweeper.
- `scripts/simulate_reports.py`: a trip with known facts, six reports, verdicts checked.

**Decisions (and why)**
- **Code decides every verdict; the model only reads and writes.** `qwen3:4b` is small enough to
  run on the dev laptop's 6 GB GPU, but not reliable enough to judge facts. It reads messy text
  well and writes decent replies, so that's all it does.
- **Keyword rules double-check the model's reading.** In the first real run the model labelled
  "the bus didn't stop at Thoraipakkam, it just drove past" as `late`, so the skipped-stop check
  never ran. Rules now win for four phrases they can't misread (skipped stop, never came,
  harassment, accident). They don't win for "early", which appears in sentences that are
  really about lateness.
- **The model can't make a report critical.** It marked "45 minutes late, I missed my class" as
  critical; that would page every admin for an ordinary delay. The model's hint is capped at
  high. Critical comes only from rules (safety reports, harassment/accident words).
- **Small models answer "not mentioned" with 0 or "".** Both are treated as null, otherwise a
  report with no number would be checked against a claimed 0 minutes.
- **Draft only.** Replies, matches and closing are admin actions, so a wrong reading costs an
  edit, not a wrong message to a student.
- **No student id in event payloads; no actor on anonymous reports.** The history screens show
  event actors, and the dashboard forwards events to route topics students subscribe to. Report
  events go to admins only, through this module's own `ops` push.
- **Structured output (`format` = JSON schema) instead of a tool-calling loop.** Same choice of
  checks, far more reliable with a 4B model, and nothing it outputs can act.
- **One analysis at a time** (semaphore): the model shares one GPU and each run holds a DB session.
- `keep_alive: 30m` and a 120 s timeout: loading the model took 53 s on the first call; later
  calls take about 6 s per report.
