import asyncio
import copy
import math
import time
from datetime import timedelta
from decimal import Decimal

import httpx

from autopilot import money, utcnow

API = "https://api.frankfurter.dev/v1"
_cache = {}
_lock = asyncio.Lock()


class MarketUnavailable(Exception):
    pass


async def fetch_json(path, params=None):
    key = (path, tuple(sorted((params or {}).items())))
    async with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() - cached[0] < 600:
            return copy.deepcopy(cached[1])
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                response = await client.get(API + path, params=params)
                response.raise_for_status()
                data = response.json()
            _cache[key] = (time.monotonic(), data)
            if len(_cache) > 100:
                _cache.pop(next(iter(_cache)))
            return copy.deepcopy(data)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise MarketUnavailable("Reference rates are temporarily unavailable. No exchange will be executed.") from exc


async def get_market():
    now = utcnow()
    start = (now.date() - timedelta(days=45)).isoformat()
    data = await fetch_json(f"/{start}..", {"from": "EUR", "to": "USD"})
    try:
        observations = [{"date": day, "rate": float(values["USD"])}
                        for day, values in sorted(data["rates"].items())]
        if not observations or any(not math.isfinite(p["rate"]) or p["rate"] <= 0 for p in observations):
            raise ValueError("Invalid provider rate")
        latest = observations[-1]
        cutoff = (now.date() - timedelta(days=30)).isoformat()
        prior = [p["rate"] for p in observations if cutoff <= p["date"] < latest["date"]]
        ordered = sorted(prior)
        benchmark = ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.75) - 1)] if ordered else None
        return {**latest, "base": "EUR", "quote": "USD", "source": "Frankfurter / ECB",
                "source_url": "https://frankfurter.dev/v1/", "fetched_at": now.isoformat(),
                "history": observations, "optimal_rate": benchmark,
                "average_rate": round(sum(prior) / len(prior), 6) if prior else None,
                "cadence": "Daily reference rates, not a live trading quote"}
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketUnavailable("The rate provider returned incomplete data. No exchange will be executed.") from exc


async def get_reference_rates(base):
    supported = await fetch_json("/currencies")
    if base not in supported:
        raise ValueError(f"Unsupported currency: {base}")
    data = await fetch_json("/latest", {"from": base})
    if not data.get("rates"):
        raise MarketUnavailable("Reference rates are unavailable.")
    data["rates"][base] = 1.0
    return data


async def reference_conversion(amount, source, target):
    amount = Decimal(str(amount))
    if not amount.is_finite() or not 0 < amount <= 1000000 or amount.as_tuple().exponent < -2:
        raise ValueError("Amount must be positive, at most 1,000,000, with at most two decimal places.")
    source, target = source.upper(), target.upper()
    data = await get_reference_rates(source)
    if target not in data["rates"]:
        raise ValueError(f"Unsupported currency: {target}")
    rate = data["rates"][target]
    return {"from_currency": source, "to_currency": target, "amount": float(amount),
            "rate": rate, "converted_amount": float(money(amount * Decimal(str(rate)))),
            "rate_date": data["date"], "source": "Frankfurter / ECB", "simulated": True}
