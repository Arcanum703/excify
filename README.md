# Excify — AI-directed EUR/HUF exchange timing

**AI-directed timing, rule-enforced safety limits.**

Live demo: https://excify-livid.vercel.app

Excify executes an authorized **simulated EUR → HUF exchange** immediately when its favorable target is met. Below the target, it waits until the final 36 hours, when Devin can decide execute/wait using real European Central Bank observations, recent trend, and the user's goal. Code—not the model—always enforces the protected minimum, deadline, pause state, and rate freshness.

No bank is connected. No money moves. Reference rates do not include a bank's fees, spreads, or settlement terms. Excify is not affiliated with Wise and does not guarantee a best future rate.

## Demo flow

1. Enter EUR and inspect the real HUF quote and its publication date.
2. Set an amount, favorable target, protected minimum, deadline, and strategy; activate AI autopilot.
3. At target, watch immediate execution; otherwise watch monitoring or a near-deadline Devin analysis. The trail records the decision source, any AI rationale/session, market evidence, and backend outcome. The card separates the latest completed check from previous AI advice.
4. Click a decision marker on the 30-day chart. Target execution, AI execution, waiting, and a safety veto are distinguished.
5. Pause the plan, refresh, or close the browser. Production decisions are delivered to the server independently of the browser.

Manual simulations remain available behind explicit confirmation and are labeled **User-confirmed**, not AI-directed.

## Where AI has authority

- **At or above target:** execute immediately on a check, including activation. No AI deliberation or API key is required for this explicit hard trigger; all safety checks still apply.
- **Below the protected minimum:** always hold, without exception.
- **Middle zone, more than 36 hours remaining:** monitor and wait without requesting AI.
- **Middle zone, 36 hours or less remaining:** in optimal mode, Devin weighs real trend and deadline pressure and may execute above the minimum. Missing/invalid AI means hold, never a silent fallback.
- **Strict target-only mode** remains an optional stricter setting: instant execution at target, no below-target execution.
- Use **Fill instant-execute demo using real rate** on the form, authorize, then activate. The button uses the latest real ECB rate as the target, a floor one HUF lower, EUR 100, and a future deadline.
- The model returns a schema-validated, final decision with a context identifier, EUR/HUF pair, observed rate/date, action, and concise rationale.
- Every applied execute/wait response is logged, including proposals rejected by safety checks.
- In the near-deadline middle zone, no valid AI response means **hold**. Target execution is an explicit authorized trigger, not an AI-failure fallback; it is logged separately.

### Hard constraints

The backend re-reads the saved plan and the latest verified market observation when the AI response arrives:

1. A completed/expired plan cannot execute again.
2. At or after the deadline, expire without a late exchange.
3. A paused plan cannot execute; pause/resume invalidates older authorizations.
4. Require a fresh, positive, verified EUR/HUF observation.
5. Never execute below the protected minimum, even if Devin says execute or the deadline is close.
6. Enforce target-only eligibility when selected.
7. Reject responses with mismatched context, changed rate/date, or an expired analysis window.
8. Store the outcome and receipt in one conditional write, so retries cannot duplicate execution.

Provider dates may be up to four calendar days old to allow weekends/holidays; older observations fail closed. Market snapshots and AI attempts expire after 30 minutes. A changed observation requires new analysis.

## Real rate chart

Frankfurter v1 supplies ECB **EUR/HUF** reference observations. Support was verified against the actual latest and historical endpoints. All conversion endpoints are restricted to EUR → HUF.

- The chart uses the last 30 calendar days of real published observations, with a calendar-date axis.
- Saved decisions are plotted at their **rate publication date and actual rate**, not invented intraday prices.
- Marker details show when the decision was applied, its rationale, and the backend result. A selector makes overlapping decisions accessible.
- Data refreshes every ten minutes while open; pending decisions refresh separately. ECB publishes daily on working days, not tick-by-tick.
- Missing data is an explicit unavailable state, never a hardcoded fallback.
- Historical safety inspection shows eligibility against real observations; it does **not** fabricate past AI decisions or claim to be an AI backtest.

## Durable async operation

Vercel Hobby Cron requests checks daily between **17:00 and 18:00 UTC**. Activation and resume also request analysis. The daily schedule requires a deadline 26 hours to 90 days ahead; dates in the form mean end-of-day UTC. Exact-minute execution is not guaranteed.

Each check reserves a persistent job before creating a Devin session. Devin receives only numeric/enum goal data and verified market history—not arbitrary user prompts or repository tasks. Organization knowledge/secrets are disabled. A session-specific, sensitive callback token authorizes only that one decision.

Devin posts its final decision to a scoped callback; the backend independently validates and applies it. This works with the browser closed. The normal check endpoint and daily worker can also reconcile saved session output if delivery fails. Temporary failures polling an existing session (timeouts, rate limits, and server errors) preserve its job and callback, with bounded polling backoff from 30 seconds up to five minutes within the original analysis window. A valid callback can complete during backoff, and the hard target trigger remains immediate. Invalid decisions and permanent failures still fail closed.

The five-minute limit on starting another AI analysis is checked inside the same conditional write that reserves the job, not just against an earlier snapshot. Overlapping checks cannot spend another AI session during that cooldown.

A conditional global budget allows **20 sessions/day by default**, bounded to one ACU each. `DEVIN_MAX_SESSIONS_PER_DAY` can change the daily cap, up to 100. This limits accidental public-demo usage of the supplied Devin account; it does not assert the account's usage is free.

## Local setup

Keep the existing Python/FastAPI and vanilla HTML/CSS/JavaScript stack. No new SDK or chart package is required. Python 3.13 recommended; deployment is also built on Vercel's supported Python runtime.

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

Open http://127.0.0.1:8000. Put secrets in the ignored `.env.local` file; never in source or chat.

| Variable | Purpose |
| --- | --- |
| `DEVIN_API_KEY` | Server-only Devin key. Personal/service legacy keys use v1; current service-user keys use v3. |
| `DEVIN_ORG_ID` | Required for v3. Legacy keys already carry organization scope. |
| `BLOB_READ_WRITE_TOKEN`, `BLOB_STORE_ID` | Private persistent Vercel Blob storage; absent locally, use SQLite. |
| `CRON_SECRET` | Authenticates Vercel's scheduled worker. |
| `EXCIFY_PUBLIC_URL` | Trusted HTTPS production origin for scoped AI callbacks. |
| `DEVIN_MAX_SESSIONS_PER_DAY` | Bounded provider usage; defaults to 20. |

Local/preview environments do not use the production callback. Use **Check AI result**, or leave the UI open to reconcile pending results. Without a Devin key, quotes, explicit manual simulations, and automatic target triggers work; near-deadline middle-zone AI timing holds. No local scheduler is implied.

## Test

```sh
python -m unittest test_target_trigger test_ai_timing test_autopilot test_api test_store test_market test_devin_timing -v
node --test test_chart.cjs test_ui.cjs
node --check static/app.js
```

Automated tests use temporary SQLite and mocked provider responses; they do not spend API credits. Coverage includes immediate target execution without AI, early middle-zone monitoring, near-deadline AI decisions, minimum/deadline/pause enforcement, callback authentication and replay, transient polling recovery, atomic cooldown races, conflicting idempotency payloads, persistence, real-shaped HUF data, chart alignment, and rendering the latest outcome separately from historical AI advice.

Live verification separately creates bounded real Devin sessions using actual ECB observations. The original `test_pattern.py` remains an exploratory sample-data script, not an execution engine. Its sample transactions use real dated HUF observations with explicitly simulated amounts/results.

## Deploy

1. Use the existing Vercel FastAPI project and private Blob store.
2. Configure the variables above as server-side Vercel environment variables. Do not overwrite `.env.local` when refreshing Vercel environment files.
3. Deploy the committed source. `vercel.json` configures the authenticated daily cron.
4. Verify the public page, quote source/date, a real AI callback with no browser polling, saved reasoning and chart markers, and the worker heartbeat.

Vercel Hobby storage has usage limits; exceeding them pauses access rather than authorizing unsafe execution. No paid service or upgrade is required for this hosting setup. The supplied Devin API uses the user's existing account.

## Persistence, migration, and security

Private plans are isolated by opaque HttpOnly, SameSite browser-session cookies (Secure on Vercel); storage uses only their hashes. Keep cookies to revisit a plan. Losing cookies does not cancel it: pause plans first.

The EUR/HUF release uses a **new currency-versioned storage namespace** and local database. Prior-pair data is preserved in its old namespace, excluded from the new UI and worker, and never relabeled or numerically reinterpreted. Create new EUR/HUF plans. Development, preview, and production are isolated from each other.

Reusing a request ID with different amounts, goals, deadlines, strategies, or operation types returns HTTP 409. Equivalent numeric formats and timezone representations are accepted. Identical retries return the saved result without fetching a new quote or starting another action; the comparison is repeated inside the atomic save and works with existing records.

Callback tokens are scoped, hashed at rest, and excluded from API responses. Provider keys never reach the browser. Pause/resume increments plan authorization versions; an old response cannot execute a newly resumed plan. Provider errors are sanitized. The demo is not a production banking ledger or financial-advice system.

## Architecture

- `autopilot.py`: decision schema, hard safety constraints, receipts, and audit events.
- `devin_timing.py`: actual Devin session creation and structured output retrieval.
- `timing_service.py`: persistent jobs, scoped delivery, revalidation, idempotency, and usage budget.
- `market.py`: verified EUR/HUF observations, recent history, and trend inputs.
- `plan_store.py`: local SQLite / private Blob with strong-ETag conditional writes.
- `autopilot_api.py`: session-isolated controls, worker, callbacks, and safety inspection.
- `static/rate-chart.js`: testable real-data chart and decision-marker geometry.
- `static/app.js`: renders AI proposals and server outcomes without overriding either.

Key routes: `/api/market`, `/api/state`, `/api/plans`, `/api/plans/{id}/check`, `/api/ai-decisions/{account}/{plan}/{attempt}`, `/api/cron`, `/api/exchange`, `/api/replay`, `/convert`, `/health`. Optional legacy local chat/voice endpoints are not the timing authority.
