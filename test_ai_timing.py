import copy
import unittest
from datetime import timedelta

from autopilot import AIDecision, PlanInput, apply_check, new_plan, set_enabled, utcnow


class AITimingTests(unittest.TestCase):
    def setUp(self):
        self.now = utcnow()
        self.plan = new_plan(PlanInput(amount=1200, target_rate=380, minimum_rate=350,
            strategy="optimal", deadline=self.now + timedelta(hours=30)), self.now)
        self.quote = {"base": "EUR", "quote": "HUF", "rate": 364.28,
                      "date": str(self.now.date()), "fetched_at": self.now.isoformat(),
                      "source": "Frankfurter / ECB", "average_rate": 370.0}
        self.decision = AIDecision(final=True, context_id="test-context", pair="EUR/HUF", action="execute",
            rate=364.28, rate_date=self.quote["date"],
            reasoning="The recent declining rate trend and the user's deadline favor securing forints now.")

    def check(self, decision=None, quote=None, plan=None, now=None):
        return apply_check(plan or self.plan, quote or self.quote, now or self.now,
                           decision=decision or self.decision, session_id="devin-test")

    def test_ai_can_execute_below_favorable_target(self):
        result = self.check()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["receipt"]["received_huf"], "437136.00")
        self.assertEqual(result["events"][-1]["ai_action"], "execute")

    def test_target_trigger_cannot_be_delayed_by_ai_wait(self):
        quote = self.quote | {"rate": 390}
        decision = self.decision.model_copy(update={"action": "wait", "rate": 390})
        result = self.check(decision=decision, quote=quote)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["events"][-1]["code"], "target_execute")

    def test_ai_cannot_override_minimum(self):
        plan = self.plan | {"minimum_rate": "370"}
        result = self.check(plan=plan)
        self.assertEqual(result["status"], "active")
        event = result["events"][-1]
        self.assertEqual(event["ai_action"], "execute")
        self.assertEqual(event["action"], "hold")
        self.assertEqual(event["code"], "below_minimum")
        self.assertEqual(event["ai_reasoning"], self.decision.reasoning)
        self.assertIsNone(result["receipt"])

    def test_ai_cannot_override_deadline(self):
        result = self.check(plan=self.plan | {"deadline": self.now.isoformat()})
        self.assertEqual(result["status"], "expired")
        self.assertIsNone(result["receipt"])
        self.assertEqual(result["events"][-1]["ai_action"], "execute")

    def test_pause_during_ai_analysis_blocks_execution(self):
        paused = set_enabled(self.plan, False, self.now)
        result = self.check(plan=paused)
        self.assertEqual(result["status"], "paused")
        self.assertIsNone(result["receipt"])
        self.assertEqual(result["events"][-1]["code"], "paused")

    def test_expiring_deadline_does_not_force_ai_to_execute(self):
        plan = self.plan | {"deadline": (self.now + timedelta(hours=1)).isoformat()}
        result = self.check(plan=plan, decision=self.decision.model_copy(update={"action": "wait"}))
        self.assertEqual(result["status"], "active")
        self.assertIsNone(result["receipt"])

    def test_target_only_remains_an_additional_user_constraint(self):
        result = self.check(plan=self.plan | {"strategy": "target_only"})
        self.assertEqual(result["events"][-1]["code"], "below_target")
        self.assertIsNone(result["receipt"])

    def test_missing_ai_never_falls_back_to_old_rules(self):
        result = apply_check(self.plan, self.quote, self.now)
        self.assertEqual(result["status"], "active")
        self.assertEqual(result["events"][-1]["code"], "ai_unavailable")
        self.assertIsNone(result["receipt"])

    def test_changed_quote_invalidates_ai_evidence(self):
        result = self.check(quote=self.quote | {"rate": 365})
        self.assertEqual(result["events"][-1]["code"], "evidence_changed")
        self.assertIsNone(result["receipt"])

    def test_stale_quote_blocks_ai_execution(self):
        quote = self.quote | {"fetched_at": (self.now - timedelta(hours=1)).isoformat()}
        self.assertIsNone(self.check(quote=quote)["receipt"])

    def test_receipt_and_ai_log_are_idempotent(self):
        result = self.check()
        self.assertEqual(self.check(plan=result), result)

    def test_decision_log_contains_rate_date_and_authority(self):
        result = self.check(decision=self.decision.model_copy(update={"action": "wait"}))
        event = result["events"][-1]
        self.assertEqual(event["rate_date"], self.quote["date"])
        self.assertEqual(event["authority"], "AI-directed timing, rule-enforced safety limits")
        self.assertEqual(event["session_id"], "devin-test")
        self.assertEqual(event["action"], "hold")

    def test_input_is_not_mutated(self):
        original = copy.deepcopy(self.plan)
        self.check()
        self.assertEqual(self.plan, original)


if __name__ == "__main__":
    unittest.main()
