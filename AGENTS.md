# Project verification

- Preserve the Python/FastAPI and vanilla HTML/CSS/JavaScript stack.
- On Windows use `.venv/Scripts/python`; on Unix use `.venv/bin/python`.
- Regression tests: `python -m unittest test_ai_timing test_autopilot test_api test_store test_market test_devin_timing -v` and `node --test test_chart.cjs`.
- EUR/HUF is the only supported pair. Never relabel prior-pair stored amounts; use the currency-versioned storage namespace.
- At/above target, execute immediately without AI after hard safety checks. In the middle zone wait beyond 36 hours; within 36 hours require a schema-valid Devin decision. Never use an AI-failure fallback. Callback and polling paths must recheck current safety limits.
- Live Devin tests consume the supplied account's allowance; unit/API tests must mock provider calls.
- Never pull Vercel environment files over `.env.local`, which contains user-maintained Devin credentials.
- JavaScript syntax check: `node --check static/app.js`.
- Local server: `python -m uvicorn main:app --host 127.0.0.1 --port 8000`.
- Never claim reference rates are streaming prices or simulated exchanges are real money movement.
- The protected minimum always overrides optimal/deadline behavior. Paused or terminal plans must not execute.
- Vercel Hobby cron runs daily, not continuously. Verify the production heartbeat separately from UI refreshes.
- Blob storage uses the existing httpx library and Blob API v12; no new SDK dependency is needed.
- Read private blobs with `cache=0` and `Accept-Encoding: identity`. Compressed responses can return weak ETags, which must not be passed to conditional writes.
- Conditional Blob writes use `x-if-match`; verify large-record updates and stale-write rejection before changing this storage path.
- `.env`, `.env.local`, `.vercel`, `.excify`, and database files must remain uncommitted.
- In Git Bash on Windows, set `MSYS_NO_PATHCONV=1` when calling `vercel api /...` to prevent API paths being converted into filesystem paths.
