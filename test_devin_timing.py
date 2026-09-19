import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx

from devin_timing import AIRetryableError, AIUnavailable, configuration, evidence, request_json, session_decision, start_session


class DevinClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.environment = patch.dict("os.environ", {"DEVIN_API_KEY": "apk_test_only", "DEVIN_ORG_ID": "org-test"})
        self.environment.start()
        self.context = {"context_id": "context-123", "pair": "EUR/HUF", "current_rate": 364.28,
                        "rate_date": "2026-09-18", "protected_minimum": "350", "deadline": "2026-09-26T23:59:00Z"}
        self.output = {"final": True, "context_id": "context-123", "pair": "EUR/HUF", "action": "wait",
                       "rate": 364.28, "rate_date": "2026-09-18", "reasoning": "Waiting because the recent trend and deadline allow time to pursue the user's target."}

    def tearDown(self):
        self.environment.stop()

    def test_personal_key_uses_supported_legacy_api(self):
        self.assertEqual(configuration()[0], "https://api.devin.ai/v1/sessions")

    def test_service_key_uses_org_scoped_api(self):
        with patch.dict("os.environ", {"DEVIN_API_KEY": "cog_test_only"}):
            self.assertEqual(configuration()[0], "https://api.devin.ai/v3/organizations/org-test/sessions")

    def schedule_context(self, hour):
        plan = {"amount": "100", "target_rate": "380", "minimum_rate": "350",
                "deadline": "2026-09-20T23:59:00Z", "strategy": "optimal", "events": []}
        quote = {"rate": 364.28, "date": "2026-09-18", "source": "Frankfurter / ECB"}
        return evidence(plan, quote, "schedule-test", datetime(2026, 9, 19, hour, tzinfo=timezone.utc))

    def test_ai_gets_same_day_upcoming_window_before_17_utc(self):
        context = self.schedule_context(12)
        self.assertEqual(context["next_scheduled_window"]["start"], "2026-09-19T17:00:00+00:00")
        self.assertEqual(context["next_scheduled_window"]["end"], "2026-09-19T18:00:00+00:00")

    def test_ai_gets_next_day_window_after_daily_window(self):
        context = self.schedule_context(19)
        self.assertEqual(context["next_scheduled_window"]["start"], "2026-09-20T17:00:00+00:00")

    async def test_session_has_bounded_access_and_secret_delivery(self):
        request = AsyncMock(return_value={"session_id": "devin-test-session"})
        with patch("devin_timing.request_json", new=request):
            await start_session(self.context, "https://excify.example/api/decision", "test-scoped-token")
        payload = request.call_args.kwargs["body"]
        self.assertEqual(payload["max_acu_limit"], 1)
        self.assertEqual(payload["secret_ids"], [])
        self.assertEqual(payload["knowledge_ids"], [])
        self.assertNotIn("test-scoped-token", payload["prompt"])
        self.assertEqual(payload["session_secrets"][0]["value"], "test-scoped-token")
        self.assertTrue(payload["session_secrets"][0]["sensitive"])
        self.assertIn("final", payload["structured_output_schema"]["required"])

    async def test_wait_and_execute_are_real_provider_outputs(self):
        for action in ("execute", "wait"):
            with patch("devin_timing.request_json", new=AsyncMock(return_value={"structured_output": self.output | {"action": action}})):
                decision = await session_decision("devin-test-session")
                self.assertEqual(decision.action, action)
                self.assertEqual(decision.reasoning, self.output["reasoning"])

    async def test_missing_output_is_pending_not_fabricated_wait(self):
        with patch("devin_timing.request_json", new=AsyncMock(return_value={"status_enum": "working", "structured_output": None})):
            self.assertIsNone(await session_decision("devin-test-session"))

    async def test_nonfinal_and_malformed_output_cannot_execute(self):
        for output in [self.output | {"final": False}, self.output | {"action": "buy_everything"}, {"action": "execute"}]:
            with patch("devin_timing.request_json", new=AsyncMock(return_value={"structured_output": output})), self.assertRaises(AIUnavailable):
                await session_decision("devin-test-session")

    async def test_finished_session_without_decision_fails_closed(self):
        with patch("devin_timing.request_json", new=AsyncMock(return_value={"status_enum": "finished"})), self.assertRaises(AIUnavailable):
            await session_decision("devin-test-session")

    async def test_temporary_http_errors_are_retryable(self):
        for status in (408, 425, 429, 500, 502, 503, 504):
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status)))
            with self.subTest(status=status), patch("devin_timing.httpx.AsyncClient", return_value=client), self.assertRaises(AIRetryableError):
                await request_json("GET", "https://api.devin.ai/v1/sessions/devin-test")

    async def test_network_timeout_is_retryable(self):
        def timeout(request):
            raise httpx.ReadTimeout("Timeout", request=request)
        client = httpx.AsyncClient(transport=httpx.MockTransport(timeout))
        with patch("devin_timing.httpx.AsyncClient", return_value=client), self.assertRaises(AIRetryableError):
            await request_json("GET", "https://api.devin.ai/v1/sessions/devin-test")

    async def test_authentication_failures_are_terminal_and_sanitized(self):
        for status in (401, 403, 404):
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, text="sensitive-provider-body")))
            with self.subTest(status=status), patch("devin_timing.httpx.AsyncClient", return_value=client), self.assertRaises(AIUnavailable) as failure:
                await request_json("GET", "https://api.devin.ai/v1/sessions/devin-test")
            self.assertNotIsInstance(failure.exception, AIRetryableError)
            self.assertNotIn("sensitive-provider-body", str(failure.exception))

    async def test_non_object_provider_response_is_terminal(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))
        with patch("devin_timing.httpx.AsyncClient", return_value=client), self.assertRaises(AIUnavailable) as failure:
            await request_json("GET", "https://api.devin.ai/v1/sessions/devin-test")
        self.assertNotIsInstance(failure.exception, AIRetryableError)

    async def test_non_https_callback_is_rejected(self):
        with self.assertRaises(AIUnavailable):
            await start_session(self.context, "http://excify.example/api/decision", "test-scoped-token")


if __name__ == "__main__":
    unittest.main()
