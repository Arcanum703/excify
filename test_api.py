import asyncio
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import plan_store
from autopilot import utcnow
from main import app
from plan_store import LocalStore, StoreConflict


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_store = plan_store._store
        plan_store._store = LocalStore(Path(self.temp.name) / "test.sqlite3")
        self.client = TestClient(app)
        self.client.get("/api/state")
        self.headers = {"X-Excify-Intent": "plan"}
        self.now = utcnow()
        self.quote = {"rate": 1.12, "date": str(self.now.date()), "source": "Frankfurter / ECB",
                      "optimal_rate": 1.15, "fetched_at": self.now.isoformat()}
        self.market = patch("autopilot_api.get_market", new=AsyncMock(return_value=self.quote))
        self.market.start()
        self.payload = {"amount": 1200, "target_rate": 1.16, "minimum_rate": 1.10,
                        "deadline": (self.now + timedelta(days=5)).isoformat(), "strategy": "optimal"}

    def tearDown(self):
        self.client.close()
        self.market.stop()
        plan_store._store = self.old_store
        self.temp.cleanup()

    def create(self):
        result = self.client.post("/api/plans", json=self.payload, headers=self.headers)
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()["plans"][0]

    def test_plan_survives_new_store_instance(self):
        plan = self.create()
        plan_store._store = LocalStore(Path(self.temp.name) / "test.sqlite3")
        self.assertEqual(self.client.get("/api/state").json()["plans"][0]["id"], plan["id"])

    def test_sessions_are_isolated(self):
        plan = self.create()
        other = TestClient(app)
        self.assertEqual(other.get("/api/state").json()["plans"], [])
        result = other.patch("/api/plans/" + plan["id"], json={"enabled": False}, headers=self.headers)
        self.assertEqual(result.status_code, 404)
        other.close()

    def test_mutation_requires_confirmation_header(self):
        self.assertEqual(self.client.post("/api/plans", json=self.payload).status_code, 403)

    def test_pause_and_resume(self):
        plan = self.create()
        for enabled, status in [(False, "paused"), (True, "active")]:
            result = self.client.patch("/api/plans/" + plan["id"], json={"enabled": enabled}, headers=self.headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["plans"][0]["status"], status)

    def test_authenticated_cron_executes_without_browser(self):
        self.create()
        self.quote["rate"] = 1.18
        worker = TestClient(app)
        with patch.dict(os.environ, {"CRON_SECRET": "test-only-secret"}):
            self.assertEqual(worker.get("/api/cron").status_code, 401)
            for _ in range(2):
                result = worker.get("/api/cron", headers={"Authorization": "Bearer test-only-secret"})
                self.assertEqual(result.status_code, 200, result.text)
        worker.close()
        plan = self.client.get("/api/state").json()["plans"][0]
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(plan["receipt"]["received_usd"], "1416.00")
        self.assertEqual(sum(e["code"] == "target_reached" for e in plan["events"]), 1)

    def test_paused_plan_is_not_executed_by_cron(self):
        plan = self.create()
        self.client.patch("/api/plans/" + plan["id"], json={"enabled": False}, headers=self.headers)
        self.quote["rate"] = 1.18
        with patch.dict(os.environ, {"CRON_SECRET": "test-only-secret"}):
            self.client.get("/api/cron", headers={"Authorization": "Bearer test-only-secret"})
        self.assertEqual(self.client.get("/api/state").json()["plans"][0]["status"], "paused")

    def test_manual_exchange_is_idempotent(self):
        payload = {"amount": 1200, "confirmation": True, "request_id": "11111111-1111-1111-1111-111111111111"}
        for _ in range(2):
            result = self.client.post("/api/exchange", json=payload, headers=self.headers)
            self.assertEqual(result.status_code, 201, result.text)
        plans = result.json()["plans"]
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]["receipt"]["received_usd"], "1344.00")

    def test_plan_creation_is_idempotent(self):
        self.payload["request_id"] = "22222222-2222-2222-2222-222222222222"
        self.create()
        self.create()
        self.assertEqual(len(self.client.get("/api/state").json()["plans"]), 1)

    def test_invalid_amounts_rejected(self):
        self.assertEqual(self.client.post("/api/plans", json=self.payload | {"amount": -1}, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post("/convert", json={"amount": -1, "from_currency": "EUR", "to_currency": "USD"}).status_code, 422)

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
