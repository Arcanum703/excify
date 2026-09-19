import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from autopilot import AIDecision, PlanInput, apply_check, new_plan, set_enabled


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 19, 17, tzinfo=timezone.utc)
        self.request = PlanInput(amount=1200, target_rate=380, minimum_rate=350,
                                 strategy="optimal", deadline=self.now + timedelta(days=5))
        self.plan = new_plan(self.request, self.now)
        self.quote = {"base": "EUR", "quote": "HUF", "rate": 364.28, "date": "2026-09-18",
                      "source": "Frankfurter / ECB", "fetched_at": self.now.isoformat()}
        self.ai = AIDecision(final=True, context_id="unit-test", pair="EUR/HUF", action="execute", rate=364.28,
                             rate_date="2026-09-18", reasoning="Test rationale based on the trend and the user's deadline.")

    def test_exact_floor_is_allowed_with_ai_approval(self):
        self.plan["minimum_rate"] = "364.28"
        self.assertEqual(apply_check(self.plan, self.quote, self.now, decision=self.ai)["status"], "completed")

    def test_deadline_expires_without_late_exchange(self):
        self.plan["deadline"] = self.now.isoformat()
        self.assertEqual(apply_check(self.plan, self.quote, self.now, decision=self.ai)["status"], "expired")

    def test_resume_does_not_extend_deadline(self):
        paused = set_enabled(self.plan, False, self.now)
        resumed = set_enabled(paused, True, self.now)
        self.assertEqual(resumed["deadline"], self.plan["deadline"])
        self.assertGreater(resumed["generation"], paused["generation"])

    def test_completed_plan_cannot_restart(self):
        completed = apply_check(self.plan, self.quote, self.now, decision=self.ai)
        with self.assertRaises(ValueError):
            set_enabled(completed, True, self.now)

    def test_expired_paused_plan_cannot_resume(self):
        self.plan["deadline"] = (self.now - timedelta(seconds=1)).isoformat()
        self.plan["status"] = "paused"
        self.assertEqual(set_enabled(self.plan, True, self.now)["status"], "expired")

    def test_stale_and_future_observations_do_not_execute(self):
        for day in ("2026-09-10", "2026-09-20"):
            result = apply_check(self.plan, self.quote | {"date": day}, self.now, decision=self.ai)
            self.assertIsNone(result["receipt"])
            self.assertEqual(result["events"][-1]["code"], "rate_unavailable")

    def test_invalid_inputs_rejected(self):
        base = self.request.model_dump()
        for changes in [{"amount": -1}, {"amount": 0}, {"amount": float("nan")},
                        {"amount": 1.001}, {"minimum_rate": 390}, {"target_rate": 0},
                        {"strategy": "magic"}, {"deadline": datetime(2026, 10, 1)}]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                PlanInput(**(base | changes))

    def test_daily_schedule_requires_sufficient_lead_time(self):
        request = self.request.model_copy(update={"deadline": self.now + timedelta(hours=2)})
        with self.assertRaises(ValueError):
            new_plan(request, self.now)

    def test_money_rounds_half_up(self):
        self.plan["amount"] = "1.00"
        quote = self.quote | {"rate": 364.285}
        decision = self.ai.model_copy(update={"rate": 364.285})
        self.assertEqual(apply_check(self.plan, quote, self.now, decision=decision)["receipt"]["received_huf"], "364.29")


if __name__ == "__main__":
    unittest.main()
