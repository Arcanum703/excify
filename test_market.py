import unittest
from unittest.mock import AsyncMock, patch

from market import MarketUnavailable, get_market, reference_conversion


class MarketTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_eur_huf_is_accepted(self):
        for source, target in [("XYZ", "HUF"), ("EUR", "GBP"), ("HUF", "EUR")]:
            with self.assertRaises(ValueError):
                await reference_conversion(100, source, target)

    async def test_dated_conversion_uses_decimal_rounding(self):
        with patch("market.get_reference_rates", new=AsyncMock(return_value={"date": "2026-09-18", "rates": {"HUF": 364.285}})):
            result = await reference_conversion(1, "eur", "huf")
            self.assertEqual(result["converted_amount"], 364.29)
            self.assertEqual(result["rate_date"], "2026-09-18")
            self.assertEqual(result["source"], "Frankfurter / ECB")

    async def test_provider_request_is_huf_and_history_is_real_shape(self):
        fetch = AsyncMock(return_value={"rates": {"2026-09-17": {"HUF": 362.98}, "2026-09-18": {"HUF": 364.28}}})
        with patch("market.fetch_json", new=fetch):
            result = await get_market()
        self.assertEqual(fetch.call_args.args[1], {"from": "EUR", "to": "HUF"})
        self.assertEqual(result["quote"], "HUF")
        self.assertEqual(result["history"][-1]["rate"], 364.28)
        self.assertIn("change_30d_pct", result["trend"])

    async def test_missing_observations_fail_closed(self):
        with patch("market.fetch_json", new=AsyncMock(return_value={"rates": {}})), self.assertRaises(MarketUnavailable):
            await get_market()

    async def test_provider_failure_is_not_replaced_by_fake_rate(self):
        with patch("market.fetch_json", new=AsyncMock(side_effect=MarketUnavailable("Unavailable"))), self.assertRaises(MarketUnavailable):
            await get_market()


if __name__ == "__main__":
    unittest.main()
