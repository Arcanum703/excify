import unittest
from unittest.mock import AsyncMock, patch

from market import MarketUnavailable, get_market, reference_conversion


class MarketTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_source_is_not_silently_converted(self):
        with patch("market.fetch_json", new=AsyncMock(return_value={"EUR": "Euro", "USD": "US Dollar"})):
            with self.assertRaises(ValueError):
                await reference_conversion(100, "XYZ", "USD")

    async def test_invalid_target_is_rejected(self):
        with patch("market.get_reference_rates", new=AsyncMock(return_value={"date": "2026-09-18", "rates": {"USD": 1.146}})):
            with self.assertRaises(ValueError):
                await reference_conversion(100, "EUR", "XYZ")

    async def test_dated_conversion_uses_decimal_rounding(self):
        with patch("market.get_reference_rates", new=AsyncMock(return_value={"date": "2026-09-18", "rates": {"USD": 1.165}})):
            result = await reference_conversion(1, "eur", "usd")
            self.assertEqual(result["converted_amount"], 1.17)
            self.assertEqual(result["rate_date"], "2026-09-18")
            self.assertEqual(result["source"], "Frankfurter / ECB")

    async def test_missing_observations_fail_closed(self):
        with patch("market.fetch_json", new=AsyncMock(return_value={"rates": {}})):
            with self.assertRaises(MarketUnavailable):
                await get_market()

    async def test_provider_failure_is_not_replaced_by_fake_rate(self):
        with patch("market.fetch_json", new=AsyncMock(side_effect=MarketUnavailable("Unavailable"))):
            with self.assertRaises(MarketUnavailable):
                await get_market()


if __name__ == "__main__":
    unittest.main()
