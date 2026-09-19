import datetime

from market import get_market, get_reference_rates, reference_conversion

# Reference data is shared by quotes, conversions, and autonomous checks.
FRANKFURTER_LATEST = "https://api.frankfurter.dev/v1/latest?from=EUR&to=USD"
FRANKFURTER_HISTORY = "https://api.frankfurter.dev/v1/{start}..{end}?from=EUR&to=USD"


async def get_rates(base: str = "USD") -> dict:
    """Fetch verified daily reference rates without invented fallbacks."""
    # Reject invalid currencies instead of returning rates in the wrong base.
    return (await get_reference_rates(base.upper()))["rates"]


async def get_current_rate() -> float:
    """Return the latest published EUR/USD reference rate."""
    return (await get_market())["rate"]


async def get_7day_average() -> float:
    """Fetch EUR/USD observations from the last seven calendar days."""
    data = await get_market()
    cutoff = str(datetime.date.today() - datetime.timedelta(days=7))
    daily_rates = data["history"]  # Dated EUR/USD observations from the provider.
    values = [day["rate"] for day in daily_rates if day["date"] >= cutoff]
    if not values:
        raise ValueError("No recent historical observations available.")
    return round(sum(values) / len(values), 4)


async def convert(amount: float, from_currency: str, to_currency: str) -> dict:
    """Convert an amount using a dated reference rate, without moving money."""
    return await reference_conversion(amount, from_currency, to_currency)
