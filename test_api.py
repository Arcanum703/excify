import asyncio
import copy
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

from fastapi.testclient import TestClient

import plan_store
from autopilot import AIDecision, utcnow
from main import app
from plan_store import LocalStore, StoreConflict


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_store = plan_store._store
        plan_store._store = LocalStore(Path(self.temp.name) / "test.sqlite3")
        self.environment = patch.dict(os.environ, {"DEVIN_API_KEY": "apk_test_only", "VERCEL_ENV": "development", "DEVIN_MAX_SESSIONS_PER_DAY": "20"})
        self.environment.start()
        self.now = utcnow()
        self.quote = {"base": "EUR", "quote": "HUF", "rate": 364.28, "date": str(self.now.date()),
                      "source": "Frankfurter / ECB", "fetched_at": self.now.isoformat(),
                      "average_rate": 365.0, "trend": {"change_30d_pct": -0.2}, "history": []}
        self.sessions = {}
        self.callbacks = {}
        self.ai_action = "wait"
        self.patches = [
            patch("timing_service.get_market", new=AsyncMock(side_effect=lambda: copy.deepcopy(self.quote))),
            patch("autopilot_api.get_market", new=AsyncMock(side_effect=lambda: copy.deepcopy(self.quote))),
            patch("timing_service.start_session", new=AsyncMock(side_effect=self.start_session)),
            patch("timing_service.session_decision", new=AsyncMock(side_effect=self.decision)),
        ]
        for mocked in self.patches:
            mocked.start()
        self.client = TestClient(app)
        self.client.get("/api/state")
        self.headers = {"X-Excify-Intent": "plan"}
        self.payload = {"amount": 1200, "target_rate": 380, "minimum_rate": 350,
                        "deadline": (self.now + timedelta(hours=30)).isoformat(), "strategy": "optimal"}

    async def start_session(self, context, callback=None, token=None):
        session = "devin-" + context["context_id"]
        self.sessions[session] = context
        self.callbacks[session] = (callback, token)
        await asyncio.sleep(0)
        return {"session_id": session, "api_version": "v1"}

    async def decision(self, session):
        context = self.sessions[session]
        return AIDecision(final=True, context_id=context["context_id"], pair="EUR/HUF", action=self.ai_action,
                          rate=context["current_rate"], rate_date=context["rate_date"],
                          reasoning="Test AI considers the real-rate-shaped fixture trend and the user's goal.")

    def tearDown(self):
        self.client.close()
        for mocked in reversed(self.patches):
            mocked.stop()
        self.environment.stop()
        plan_store._store = self.old_store
        self.temp.cleanup()

    def create(self):
        result = self.client.post("/api/plans", json=self.payload, headers=self.headers)
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()["plans"][0]

    def finish(self, plan):
        result = self.client.post("/api/plans/" + plan["id"] + "/check", json={}, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["plans"][0]

    def test_newly_fetched_quote_can_start_ai_analysis(self):
        async def fresh_quote():
            await asyncio.sleep(0.01)
            return self.quote | {"fetched_at": utcnow().isoformat()}
        with patch("timing_service.get_market", new=AsyncMock(side_effect=fresh_quote)):
            plan = self.create()
        self.assertIsNotNone(plan["pending_ai"])
        self.assertEqual(len(self.sessions), 1)

    def test_target_creation_executes_without_ai_or_credentials(self):
        self.payload["target_rate"] = 364.28
        self.payload["minimum_rate"] = 363
        with patch.dict(os.environ, {"DEVIN_API_KEY": ""}):
            plan = self.create()
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(plan["receipt"]["received_huf"], "437136.00")
        self.assertEqual(plan["events"][-1]["code"], "target_execute")
        self.assertEqual(len(self.sessions), 0)
        self.assertIsNone(plan["pending_ai"])

    def test_early_middle_zone_does_not_call_ai(self):
        self.payload["deadline"] = (self.now + timedelta(days=3)).isoformat()
        plan = self.create()
        self.assertEqual(plan["events"][-1]["code"], "monitoring_window")
        self.assertEqual(len(self.sessions), 0)

    def test_scheduled_target_check_bypasses_pending_ai_and_cooldown(self):
        self.create()
        self.quote["rate"] = 380
        with patch.dict(os.environ, {"CRON_SECRET": "test-only-secret"}):
            response = self.client.get("/api/cron", headers={"Authorization":"Bearer test-only-secret"})
        self.assertEqual(response.status_code, 200, response.text)
        plan = self.client.get("/api/state").json()["plans"][0]
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(plan["events"][-1]["code"], "target_execute")
        self.assertEqual(len(self.sessions), 1)
        self.assertIsNone(plan["pending_ai"])

    def test_ai_wait_is_logged_and_does_not_execute(self):
        plan = self.finish(self.create())
        self.assertEqual(plan["status"], "active")
        self.assertEqual(plan["events"][-1]["ai_action"], "wait")
        self.assertIn("user's goal", plan["events"][-1]["ai_reasoning"])

    def test_plan_survives_new_store_instance(self):
        plan = self.create()
        plan_store._store = LocalStore(Path(self.temp.name) / "test.sqlite3")
        self.assertEqual(self.client.get("/api/state").json()["plans"][0]["id"], plan["id"])

    def test_sessions_are_isolated(self):
        plan = self.create()
        with TestClient(app) as other:
            self.assertEqual(other.get("/api/state").json()["plans"], [])
            result = other.patch("/api/plans/" + plan["id"], json={"enabled": False}, headers=self.headers)
            self.assertEqual(result.status_code, 404)

    def test_mutation_requires_confirmation_header(self):
        self.assertEqual(self.client.post("/api/plans", json=self.payload).status_code, 403)

    def test_pause_and_resume_invalidate_old_job(self):
        plan = self.create()
        old_job = plan["pending_ai"]["id"]
        for enabled, status in [(False, "paused"), (True, "active")]:
            result = self.client.patch("/api/plans/" + plan["id"], json={"enabled": enabled}, headers=self.headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["plans"][0]["status"], status)
        self.assertNotEqual(result.json()["plans"][0]["pending_ai"]["id"], old_job)

    def test_authenticated_cron_applies_ai_without_browser(self):
        self.create()
        self.ai_action = "execute"
        with TestClient(app) as worker, patch.dict(os.environ, {"CRON_SECRET": "test-only-secret"}):
            self.assertEqual(worker.get("/api/cron").status_code, 401)
            for _ in range(2):
                result = worker.get("/api/cron", headers={"Authorization": "Bearer test-only-secret"})
                self.assertEqual(result.status_code, 200, result.text)
        plan = self.client.get("/api/state").json()["plans"][0]
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(plan["receipt"]["received_huf"], "437136.00")
        self.assertEqual(sum(e["code"] == "ai_execute" for e in plan["events"]), 1)

    def test_ai_execute_below_minimum_is_vetoed_end_to_end(self):
        plan = self.create()
        self.quote["rate"] = 349
        self.ai_action = "execute"
        plan = self.finish(plan)
        self.assertEqual(plan["status"], "active")
        self.assertIsNone(plan["receipt"])
        self.assertEqual(plan["events"][-1]["ai_action"], "execute")
        self.assertEqual(plan["events"][-1]["action"], "hold")
        self.assertEqual(plan["events"][-1]["code"], "below_minimum")

    def test_paused_plan_cannot_be_executed_by_ai(self):
        plan = self.create()
        self.client.patch("/api/plans/" + plan["id"], json={"enabled": False}, headers=self.headers)
        self.ai_action = "execute"
        result = self.finish(plan)
        self.assertEqual(result["status"], "paused")
        self.assertIsNone(result["receipt"])

    def test_scoped_callback_is_authenticated_and_idempotent(self):
        with patch.dict(os.environ, {"VERCEL_ENV": "production", "EXCIFY_PUBLIC_URL": "https://excify.example"}):
            plan = self.create()
        session = plan["pending_ai"]["session_id"]
        callback, token = self.callbacks[session]
        self.ai_action = "execute"
        decision = asyncio.run(self.decision(session)).model_dump()
        with TestClient(app) as worker:
            self.assertEqual(worker.post(urlparse(callback).path, json=decision).status_code, 401)
            for _ in range(2):
                response = worker.post(urlparse(callback).path, json=decision, headers={"Authorization": "Bearer " + token})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "completed")
        plan = self.client.get("/api/state").json()["plans"][0]
        self.assertEqual(sum(e["code"] == "ai_execute" for e in plan["events"]), 1)
        self.assertNotIn("token_hash", json.dumps(plan))

    def test_changed_rate_requires_new_ai_evidence(self):
        plan = self.create()
        self.ai_action = "execute"
        self.quote["rate"] = 365
        result = self.finish(plan)
        self.assertEqual(result["events"][-1]["code"], "evidence_changed")
        self.assertIsNone(result["receipt"])

    def test_expired_analysis_starts_a_fresh_check_without_skipping_a_day(self):
        plan = self.create()
        old_job = plan["pending_ai"]["id"]
        async def expire_job():
            store = plan_store.get_store()
            key = (await store.keys())[0]
            record, version = await store.read(key)
            record["plans"][0]["pending_ai"]["expires_at"] = (utcnow() - timedelta(minutes=1)).isoformat()
            record["plans"][0]["last_requested_at"] = (utcnow() - timedelta(hours=1)).isoformat()
            await store.write(key, record, version)
        asyncio.run(expire_job())
        refreshed = self.finish(plan)
        self.assertIsNotNone(refreshed["pending_ai"])
        self.assertNotEqual(refreshed["pending_ai"]["id"], old_job)
        self.assertEqual(len(self.sessions), 2)
        self.assertIsNone(refreshed["receipt"])

    def test_budget_blocks_additional_sessions(self):
        with patch.dict(os.environ, {"DEVIN_MAX_SESSIONS_PER_DAY": "1"}):
            self.create()
            second = self.create()
        self.assertEqual(len(self.sessions), 1)
        self.assertEqual(second["events"][-1]["code"], "ai_error")
        self.assertIsNone(second["receipt"])

    def test_manual_exchange_is_explicit_and_idempotent(self):
        payload = {"amount": 1200, "confirmation": True, "request_id": "11111111-1111-1111-1111-111111111111"}
        for _ in range(2):
            result = self.client.post("/api/exchange", json=payload, headers=self.headers)
            self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(len(result.json()["plans"]), 1)
        self.assertEqual(result.json()["plans"][0]["receipt"]["received_huf"], "437136.00")
        self.assertEqual(len(self.sessions), 0)

    def test_plan_creation_is_idempotent(self):
        self.payload["request_id"] = "22222222-2222-2222-2222-222222222222"
        self.create()
        self.create()
        self.assertEqual(len(self.client.get("/api/state").json()["plans"]), 1)
        self.assertEqual(len(self.sessions), 1)

    def test_invalid_amounts_and_other_pairs_rejected(self):
        self.assertEqual(self.client.post("/api/plans", json=self.payload | {"amount": -1}, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post("/convert", json={"amount": 1, "from_currency": "EUR", "to_currency": "GBP"}).status_code, 422)

    def test_transient_poll_failure_preserves_authenticated_callback(self):
        from devin_timing import AIRetryableError
        with patch.dict(os.environ, {"VERCEL_ENV": "production", "EXCIFY_PUBLIC_URL": "https://excify.example"}):
            plan = self.create()
        job = plan["pending_ai"]
        callback, token = self.callbacks[job["session_id"]]
        with patch("timing_service.session_decision", new=AsyncMock(side_effect=AIRetryableError("Temporary HTTP 503"))) as poll:
            retrying = self.finish(plan)
            self.assertEqual(retrying["pending_ai"]["id"], job["id"])
            self.assertIsNotNone(retrying["pending_ai"]["retry_at"])
            self.finish(plan)
            self.assertEqual(poll.await_count, 1)
        self.ai_action = "execute"
        decision = asyncio.run(self.decision(job["session_id"])).model_dump()
        response = self.client.post(urlparse(callback).path, json=decision, headers={"Authorization": "Bearer " + token})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(len(self.sessions), 1)

    def test_transient_retry_recovers_without_a_new_session(self):
        from datetime import datetime
        from devin_timing import AIRetryableError
        plan = self.create()
        with patch("timing_service.session_decision", new=AsyncMock(side_effect=AIRetryableError("Temporary timeout"))):
            retrying = self.finish(plan)
        retry_at = datetime.fromisoformat(retrying["pending_ai"]["retry_at"])
        with patch("timing_service.utcnow", return_value=retry_at + timedelta(seconds=1)), patch("timing_service.session_decision", new=AsyncMock(return_value=None)):
            recovered = self.finish(plan)
        self.assertIsNone(recovered["pending_ai"]["retry_at"])
        self.ai_action = "execute"
        done = self.finish(plan)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(len(self.sessions), 1)

    def test_target_trigger_bypasses_retry_backoff(self):
        from devin_timing import AIRetryableError
        plan = self.create()
        with patch("timing_service.session_decision", new=AsyncMock(side_effect=AIRetryableError("Temporary timeout"))):
            self.finish(plan)
        self.quote["rate"] = 380
        with patch("timing_service.session_decision", new=AsyncMock(side_effect=AssertionError("Target execution must not poll AI"))):
            done = self.finish(plan)
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["events"][-1]["code"], "target_execute")

    def test_permanent_ai_failure_still_fails_closed(self):
        from devin_timing import AIUnavailable
        plan = self.create()
        with patch("timing_service.session_decision", new=AsyncMock(side_effect=AIUnavailable("Invalid final decision"))):
            failed = self.finish(plan)
        self.assertIsNone(failed["pending_ai"])
        self.assertIsNone(failed["receipt"])
        self.assertEqual(failed["events"][-1]["code"], "ai_error")

    def test_idempotent_plan_retry_accepts_equivalent_numbers_and_timezones(self):
        from datetime import datetime, timezone
        self.payload["request_id"] = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        plan = self.create()
        deadline = datetime.fromisoformat(self.payload["deadline"]).astimezone(timezone(timedelta(hours=2))).isoformat()
        response = self.client.post("/api/plans", json=self.payload | {"amount": "1200.00", "target_rate": "380.0", "minimum_rate": "350.00", "deadline": deadline}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["plans"][0]["id"], plan["id"])
        self.assertEqual(len(self.sessions), 1)

    def test_conflicting_payload_is_rejected_inside_atomic_save(self):
        import hashlib
        from autopilot import PlanInput, new_plan
        from autopilot_api import save_new
        from fastapi import HTTPException
        async def scenario():
            key = "account-" + hashlib.sha256(self.client.cookies.get("excify_session").encode()).hexdigest()
            request = PlanInput(**(self.payload | {"request_id": "abababab-abab-abab-abab-abababababab"}))
            first, second = new_plan(request), new_plan(request.model_copy(update={"amount": request.amount + 1}))
            results = await asyncio.gather(save_new(key, first), save_new(key, second), return_exceptions=True)
            conflicts = [r for r in results if isinstance(r, HTTPException)]
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0].status_code, 409)
            stored, _ = await plan_store.get_store().read(key)
            self.assertEqual(len(stored["plans"]), 1)
        asyncio.run(scenario())

    def test_overlapping_checks_cannot_bypass_cooldown(self):
        import hashlib
        from autopilot import PlanInput, new_plan
        from autopilot_api import save_new
        from timing_service import check_plan
        async def scenario():
            key = "account-" + hashlib.sha256(self.client.cookies.get("excify_session").encode()).hexdigest()
            plan = new_plan(PlanInput(**self.payload))
            await save_new(key, plan)
            entered, release = asyncio.Event(), asyncio.Event()
            calls = 0
            async def market():
                nonlocal calls
                calls += 1
                if calls == 1:
                    entered.set()
                    await release.wait()
                return copy.deepcopy(self.quote)
            with patch("timing_service.get_market", new=AsyncMock(side_effect=market)):
                delayed = asyncio.create_task(check_plan(key, plan["id"]))
                await asyncio.wait_for(entered.wait(), 2)
                try:
                    await check_plan(key, plan["id"])
                    await check_plan(key, plan["id"])
                finally:
                    release.set()
                    await delayed
            self.assertEqual(len(self.sessions), 1)
        asyncio.run(scenario())

    def test_conflicting_manual_idempotency_payload_is_rejected(self):
        payload = {"amount": 100, "confirmation": True, "request_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}
        first = self.client.post("/api/exchange", json=payload, headers=self.headers)
        second = self.client.post("/api/exchange", json=payload | {"amount": 200}, headers=self.headers)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 409)
        saved = self.client.get("/api/state").json()["plans"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["receipt"]["sent_eur"], "100.00")

    def test_conflicting_plan_fields_are_rejected(self):
        self.payload["request_id"] = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        self.create()
        for changed in ({"amount": 200}, {"target_rate": 390}, {"minimum_rate": 355},
                        {"strategy": "target_only"}, {"deadline": (self.now + timedelta(hours=32)).isoformat()}):
            with self.subTest(changed=changed):
                response = self.client.post("/api/plans", json=self.payload | changed, headers=self.headers)
                self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.sessions), 1)

    def test_idempotency_keys_cannot_cross_operation_types(self):
        self.payload["request_id"] = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        self.create()
        response = self.client.post("/api/exchange", json={"amount": 1200, "confirmation": True,
            "request_id": self.payload["request_id"]}, headers=self.headers)
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.client.get("/api/state").json()["plans"][0]["receipt"])

    def test_manual_request_id_cannot_be_reused_as_a_plan(self):
        payload = {"amount": 100, "confirmation": True, "request_id": "acacacac-acac-acac-acac-acacacacacac"}
        saved = self.client.post("/api/exchange", json=payload, headers=self.headers).json()["plans"][0]
        plan_payload = {key: saved[key] for key in ("amount", "target_rate", "minimum_rate", "deadline", "strategy", "request_id")}
        response = self.client.post("/api/plans", json=plan_payload, headers=self.headers)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.sessions), 0)

    def test_identical_manual_retry_does_not_require_market_availability(self):
        from market import MarketUnavailable
        payload = {"amount": 100, "confirmation": True, "request_id": "dddddddd-dddd-dddd-dddd-dddddddddddd"}
        first = self.client.post("/api/exchange", json=payload, headers=self.headers)
        with patch("autopilot_api.get_market", new=AsyncMock(side_effect=MarketUnavailable("Offline"))) as market:
            retry = self.client.post("/api/exchange", json=payload, headers=self.headers)
        self.assertEqual(retry.status_code, 201, retry.text)
        self.assertEqual(retry.json(), first.json())
        market.assert_not_awaited()

    def test_identical_plan_retry_is_not_revalidated_as_new_plan(self):
        self.payload["request_id"] = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        self.create()
        with patch("autopilot_api.new_plan", side_effect=AssertionError("An identical retry must not create a new plan")):
            retry = self.client.post("/api/plans", json=self.payload, headers=self.headers)
        self.assertEqual(retry.status_code, 201, retry.text)
        self.assertEqual(len(self.sessions), 1)

    def test_optimistic_concurrency_rejects_stale_write(self):
        async def scenario():
            store = plan_store.get_store()
            await store.write("test", {"status": "active"}, None)
            _, version = await store.read("test")
            await store.write("test", {"status": "paused"}, version)
            with self.assertRaises(StoreConflict):
                await store.write("test", {"status": "completed"}, version)
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
