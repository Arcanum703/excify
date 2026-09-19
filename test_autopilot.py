import copy
import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from autopilot import PlanInput, apply_check, new_plan, set_enabled


class AutopilotTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 19, 17, tzinfo=timezone.utc)
        self.request = PlanInput(amount=1200, target_rate=1.16, minimum_rate=1.10,
                                 strategy="optimal", deadline=self.now + timedelta(days=5))
        self.plan = new_plan(self.request, self.now)
        self.quote = {"rate": 1.12, "date": "2026-09-18", "source": "Frankfurter / ECB",
                      "optimal_rate": 1.15, "fetched_at": self.now.isoformat()}

    def check(self, **changes):
        quote = dict(self.quote, **changes)
        return apply_check(self.plan, quote, self.now)

    def test_favorable_target_executes(self):
        result = self.check(rate=1.16)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["receipt"]["received_usd"], "1392.00")
        self.assertTrue(result["receipt"]["simulated"])

    def test_historical_benchmark_executes_in_optimal_mode(self):
        self.assertEqual(self.check(rate=1.15)["status"], "completed")

    def test_target_only_ignores_historical_benchmark(self):
        self.plan["strategy"] = "target_only"
        self.assertEqual(self.check(rate=1.15)["status"], "active")

    def test_waits_when_deadline_is_far_away(self):
        self.assertEqual(self.check()["status"], "active")

    def test_optimal_accepts_above_floor_near_deadline(self):
        self.plan["deadline"] = (self.now + timedelta(hours=30)).isoformat()
        result = self.check()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["events"][-1]["code"], "deadline_window")

    def test_floor_overrides_deadline(self):
        self.plan["deadline"] = (self.now + timedelta(hours=1)).isoformat()
        result = self.check(rate=1.09)
        self.assertEqual(result["status"], "active")
        self.assertIsNone(result["receipt"])
        self.assertEqual(result["events"][-1]["code"], "below_minimum")

    def test_exact_floor_is_allowed(self):
        self.plan["deadline"] = (self.now + timedelta(hours=1)).isoformat()
        self.assertEqual(self.check(rate=1.10)["status"], "completed")

    def test_deadline_expires_without_late_exchange(self):
        self.plan["deadline"] = self.now.isoformat()
        self.assertEqual(self.check(rate=1.20)["status"], "expired")

    def test_target_only_does_not_relax_near_deadline(self):
        self.plan["strategy"] = "target_only"
        self.plan["deadline"] = (self.now + timedelta(hours=1)).isoformat()
        self.assertEqual(self.check()["status"], "active")

    def test_paused_plan_cannot_execute(self):
        self.plan = set_enabled(self.plan, False, self.now)
        self.assertEqual(self.check(rate=1.20)["status"], "paused")

    def test_resume_does_not_extend_deadline(self):
        deadline = self.plan["deadline"]
        paused = set_enabled(self.plan, False, self.now)
        resumed = set_enabled(paused, True, self.now)
        self.assertEqual(resumed["deadline"], deadline)
        self.assertEqual(resumed["status"], "active")

    def test_completed_plan_is_idempotent(self):
        completed = self.check(rate=1.20)
        self.assertEqual(apply_check(completed, self.quote, self.now), completed)
        with self.assertRaises(ValueError):
            set_enabled(completed, True, self.now)

    def test_expired_paused_plan_cannot_resume(self):
        self.plan["deadline"] = (self.now - timedelta(seconds=1)).isoformat()
        self.plan["status"] = "paused"
        self.assertEqual(set_enabled(self.plan, True, self.now)["status"], "expired")

    def test_stale_rates_do_not_execute(self):
        self.assertEqual(self.check(rate=1.20, date="2026-09-10")["status"], "active")
        self.assertEqual(self.check(rate=1.20, date="2026-09-10")["events"][-1]["code"], "rate_unavailable")

    def test_future_rates_do_not_execute(self):
        self.assertEqual(self.check(rate=1.20, date="2026-09-20")["status"], "active")

    def test_old_fetch_does_not_execute(self):
        result = self.check(rate=1.20, fetched_at=(self.now - timedelta(hours=1)).isoformat())
        self.assertEqual(result["status"], "active")

    def test_missing_market_data_holds_but_still_expires(self):
        result = apply_check(self.plan, None, self.now)
        self.assertEqual(result["status"], "active")
        self.plan["deadline"] = self.now.isoformat()
        self.assertEqual(apply_check(self.plan, None, self.now)["status"], "expired")

    def test_check_does_not_mutate_original(self):
        original = copy.deepcopy(self.plan)
        self.check(rate=1.20)
        self.assertEqual(self.plan, original)

    def test_invalid_inputs_rejected(self):
        base = self.request.model_dump()
        for changes in [{"amount": -1}, {"amount": 0}, {"amount": float("nan")},
                        {"amount": 1.001}, {"minimum_rate": 1.20}, {"target_rate": 0},
                        {"strategy": "magic"}, {"deadline": datetime(2026, 10, 1)}]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                PlanInput(**(base | changes))

    def test_daily_schedule_requires_sufficient_lead_time(self):
        request = self.request.model_copy(update={"deadline": self.now + timedelta(hours=2)})
        with self.assertRaises(ValueError):
            new_plan(request, self.now)

    def test_money_rounds_half_up(self):
        self.plan["amount"] = "1.00"
        self.assertEqual(self.check(rate=1.165)["receipt"]["received_usd"], "1.17")


if __name__ == "__main__":
    unittest.main()
