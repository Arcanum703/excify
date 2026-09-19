# Excify Autopilot

**Set a target. Protect your minimum. Let the agent check rates while you are away.**

Live demo: https://excify-livid.vercel.app

Excify is an independent EUR → USD exchange-planning prototype, not affiliated with Wise. It uses real, dated ECB reference rates and performs **simulated exchanges only**. It does not hold funds, contact a bank, or move real money.

## One complete flow

1. Enter an amount and review the reference-rate conversion.
2. Confirm a simulated exchange to receive a persistent receipt, or activate an autopilot plan.
3. Set your favorable target, protected minimum, deadline, and strategy.
4. Pause/resume a plan, inspect its decision log, and return later to its saved result.
5. Test the same rules against seven real historical observations using the clearly labeled replay.

The dashboard retains the original vanilla HTML/CSS/JavaScript and Python/FastAPI stack. No frontend framework or paid FX API is required.

## What is real, and what is simulated?

| Component | Behavior |
| --- | --- |
| Rates and history | Real Frankfurter v1 / European Central Bank daily reference observations; source and date displayed |
| Market timing | Deterministic, auditable rules, not a prediction of the best future price |
| Background checks | Vercel Cron, daily between 17:00 and 18:00 UTC in production; works with the browser closed |
| Plan state and receipts | Private Vercel Blob with conditional writes; SQLite for local development without Blob credentials |
| Execution | Simulation at a reference rate; fees, spreads, settlement, and bank transfers are not modeled |
| Historical replay | Uses actual past observations and only earlier observations for its benchmark; does not change saved plans |
| AI | Execution is rule-based. Optional legacy Cerebras chat is local-only. Devin analysis is not connected yet. No LLM is required or claimed for these decisions. |
| Bundled transaction history | Sample data from the original prototype; not used as the current market benchmark |

## Execution rules, in priority order

1. **Finished plans stay finished.** Repeated jobs cannot execute a completed plan again.
2. **Deadline reached:** expire without a late exchange. Resuming does not extend the deadline.
3. **Paused:** no execution.
4. **Unavailable, invalid, or stale rate:** hold safely. No fabricated fallback rates.
5. **Below the protected minimum:** never exchange, regardless of deadline or strategy.
6. **At or above the favorable target:** execute a simulation.
7. **Optimal mode, final 36 hours:** accept an eligible rate at or above the minimum.
8. **Optimal mode, otherwise:** execute when the rate meets the upper-quartile benchmark of prior observations from the recent 30 calendar days.
9. Otherwise, hold. Target-only mode never relaxes its target.

Rates must have a valid positive value, a provider date no more than four calendar days old (allowing weekends/holidays), and a server snapshot no more than 30 minutes old. Longer holiday gaps fail closed rather than inventing a price. Reference data is cached for ten minutes. Free daily scheduling requires a deadline between 26 hours and 90 days ahead; the dashboard uses the end of the selected UTC date. This is not exact-minute scheduling or intraday market monitoring.

## Run locally

Python 3.13 recommended; use an isolated virtual environment.

```sh
python -m venv .venv
```

Windows:

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

macOS/Linux:

```sh
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. No keys are needed locally. Without a Blob token, state lives in ignored `.excify/plans.sqlite3`; there is no automatic local scheduler. Use **Check now** or invoke the authenticated job endpoint. `.env` and `.env.local` are loaded if present and must never be committed.

## Verify

```sh
python -m unittest test_autopilot test_api test_store test_market -v
node --check static/app.js
```

Tests cover the protected floor, exact threshold boundaries, deadline behavior, pause/resume, stale data, input validation, isolated sessions, persistent storage, duplicate execution, a worker request without browser cookies, and the Blob HTTP contract (including strong ETags and pagination). Unit/API tests use temporary SQLite and mocked rates: no provider keys or cloud writes.

The original `test_pattern.py` is an exploratory sample-history script, not the regression test suite.

## Vercel deployment

1. Create a Vercel FastAPI project and connect a **private** Blob store.
2. Make `BLOB_READ_WRITE_TOKEN` and `BLOB_STORE_ID` available to the appropriate environments. The token is server-only.
3. Generate a strong random `CRON_SECRET` and add it to Production. Never put it in the repository or browser code.
4. Deploy this directory. `vercel.json` schedules `GET /api/cron` daily; Vercel supplies `Authorization: Bearer <CRON_SECRET>`.
5. Verify `/health`, create a plan, trigger the deployed cron, and inspect the server heartbeat and persisted last-check time. A page refresh or an open browser is not the scheduler.

Hobby Blob is free within its limits and blocks access rather than charging overages. Monitor usage: this prototype limits sessions to ten saved plans and refuses new sessions at its demo capacity. It is not a production banking ledger. Deployment protection must allow the intended audience to open the production URL; do not expose store credentials to bypass it.

The old Render configuration remains as an alternative HTTP host, but does not configure an unattended worker by itself. Do not claim background operation on Render without arranging a scheduler.

## API and architecture

| Route | Purpose |
| --- | --- |
| `GET /api/market` | Dated quote, historical observations, benchmark and freshness |
| `GET /api/state` | Private browser-session plans and global scheduler heartbeat |
| `POST /api/plans` | Authorize a saved autopilot plan and check immediately |
| `PATCH /api/plans/{id}` | Pause/resume only a plan in the current session |
| `POST /api/plans/{id}/check` | Manual check, limited to once per five minutes |
| `POST /api/exchange` | Explicit simulated exchange with an idempotency key |
| `POST /api/replay` | Historical evaluation, never saved as a live plan |
| `GET /api/cron` | Secret-protected server job, independent of browser cookies |
| `POST /convert` | Validated reference conversion for provider-supported currencies |
| `POST /deposit` | Legacy quote-only endpoint; does not claim execution |
| `GET /history` | Explicitly labeled bundled sample history |
| `GET /health` | Health check |

`autopilot.py` is the pure decision engine. `market.py` owns verified provider data. `plan_store.py` implements optimistic concurrency. `autopilot_api.py` combines session isolation, confirmations, state transitions, replay, and the worker. `main.py` serves the app and legacy endpoints. `static/app.js` renders server results rather than overriding decisions.

Anonymous demo sessions use opaque, HttpOnly, SameSite cookies (Secure in deployment); the store uses only their hashes. Losing cookies loses access to that session, but does not cancel server-side plans. Pause plans before clearing cookies. Concurrent pause/job writes are retried against the latest stored state. State changes and receipts are stored in the same conditional write. Development, preview, and production use separate storage namespaces so a production job cannot execute a development plan.

## Demo script

- Convert EUR 1,200, review the date/source, confirm a simulation, and refresh to show its saved receipt.
- Create a **Target only** plan with a target above the current quote and a minimum below it. Show that it is waiting; pause and resume it.
- Switch to **Optimal**, choose sensible bounds around the visible historical range, and replay the recent observations. Explain each decision and the hard minimum.
- Show the server schedule and last successful worker run. Clearly separate real reference data, historical replay, and simulated execution.
