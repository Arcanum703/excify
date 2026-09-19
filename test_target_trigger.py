import unittest
from datetime import timedelta

from autopilot import AIDecision, PlanInput, apply_check, new_plan, utcnow


class TargetTriggerTests(unittest.TestCase):
    def setUp(self):
        self.now = utcnow()
        self.plan = new_plan(PlanInput(amount=100, target_rate=364.28, minimum_rate=363,
                                      deadline=self.now + timedelta(days=3)), self.now)
        self.quote = {"base":"EUR", "quote":"HUF", "rate":364.28, "date":str(self.now.date()),
                      "fetched_at":self.now.isoformat(), "source":"Frankfurter / ECB"}

    def test_exact_target_executes_without_ai(self):
        result = apply_check(self.plan, self.quote, self.now)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["events"][-1]["code"], "target_execute")
        self.assertEqual(result["receipt"]["received_huf"], "36428.00")
        self.assertIsNone(result["events"][-1]["ai_action"])

    def test_above_target_executes_without_ai(self):
        self.assertEqual(apply_check(self.plan, self.quote | {"rate":365}, self.now)["status"], "completed")

    def test_minimum_is_never_overridden(self):
        result = apply_check(self.plan, self.quote | {"rate":362}, self.now)
        self.assertIsNone(result["receipt"])
        self.assertEqual(result["events"][-1]["code"], "below_minimum")

    def test_paused_expired_and_stale_target_cannot_execute(self):
        cases = [(self.plan | {"status":"paused"},self.quote),
                 (self.plan | {"deadline":self.now.isoformat()},self.quote),
                 (self.plan,self.quote | {"fetched_at":(self.now-timedelta(hours=1)).isoformat()})]
        for plan, quote in cases:
            self.assertIsNone(apply_check(plan, quote, self.now)["receipt"])

    def test_middle_zone_waits_with_more_than_36_hours(self):
        result = apply_check(self.plan, self.quote | {"rate":364}, self.now)
        self.assertEqual(result["events"][-1]["code"], "monitoring_window")
        self.assertIsNone(result["receipt"])

    def test_middle_zone_requires_ai_at_36_hours(self):
        plan = self.plan | {"deadline":(self.now+timedelta(hours=36)).isoformat()}
        quote = self.quote | {"rate":364}
        self.assertEqual(apply_check(plan,quote,self.now)["events"][-1]["code"], "ai_unavailable")
        decision = AIDecision(final=True,context_id="test",pair="EUR/HUF",action="execute",rate=364,
            rate_date=quote["date"],reasoning="36 hours remain and the declining trend makes deadline risk more important than the target.")
        result = apply_check(plan,quote,self.now,decision=decision)
        self.assertEqual(result["status"], "completed")
        self.assertIn("36.0 hours",result["reason"])

    def test_instant_receipt_is_idempotent(self):
        done = apply_check(self.plan,self.quote,self.now)
        self.assertEqual(apply_check(done,self.quote,self.now),done)


if __name__ == "__main__":
    unittest.main()
