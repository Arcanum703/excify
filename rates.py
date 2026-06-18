import datetime
import httpx

# Fallback rates if the external API is unavailable
FALLBACK_RATES = {
    "USD": 1.0,
    "EUR": 0.925,
    "GBP": 0.787,
    "JPY": 155.0,
    "CAD": 1.37,
    "AUD": 1.52,
    "CHF": 0.905,
    "CNY": 7.25,
    "INR": 83.5,
    "MXN": 17.1,
}

EXCHANGE_API_URL = "https://open.er-api.com/v6/latest/{base}"
FRANKFURTER_LATEST = "https://api.frankfurter.dev/v1/latest?from=EUR&to=USD"
FRANKFURTER_HISTORY = "https://api.frankfurter.dev/v1/{start}..{end}?from=EUR&to=USD"
EUR_USD_FALLBACK = 1.08


async def get_rates(base: str = "USD") -> dict:
    """Fetch live exchange rates from the open API, falling back to static rates."""
    base = base.upper()
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
        try:
            resp = await client.get(EXCHANGE_API_URL.format(base=base))
            resp.raise_for_status()
            data = resp.json()
            return data.get("rates", FALLBACK_RATES)
        except Exception:
            # Normalise fallback rates relative to the requested base
            if base not in FALLBACK_RATES:
                return FALLBACK_RATES
            base_rate = FALLBACK_RATES[base]
            return {k: round(v / base_rate, 6) for k, v in FALLBACK_RATES.items()}


async def get_current_rate() -> float:
    """Fetch the live EUR/USD rate. Returns fallback 1.08 if the request fails."""
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
        try:
            resp = await client.get(FRANKFURTER_LATEST)
            resp.raise_for_status()
            return resp.json()["rates"]["USD"]
        except Exception:
            return EUR_USD_FALLBACK


async def get_7day_average() -> float:
    """Fetch EUR/USD rates for the last 7 days and return the average."""
    today = datetime.date.today()
    start = today - datetime.timedelta(days=7)
    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
        try:
            resp = await client.get(
                FRANKFURTER_HISTORY.format(start=start, end=today)
            )
            resp.raise_for_status()
            daily_rates = resp.json()["rates"]  # {"YYYY-MM-DD": {"USD": ...}, ...}
            values = [day["USD"] for day in daily_rates.values()]
            return round(sum(values) / len(values), 4)
        except Exception:
            return EUR_USD_FALLBACK


async def convert(amount: float, from_currency: str, to_currency: str) -> dict:
    """Convert an amount between two currencies."""
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    rates = await get_rates(from_currency)
    if to_currency not in rates:
        raise ValueError(f"Unsupported currency: {to_currency}")
    rate = rates[to_currency]
    converted = round(amount * rate, 2)
    return {
        "from_currency": from_currency,
        "to_currency": to_currency,
        "amount": amount,
        "rate": rate,
        "converted_amount": converted,
    }
