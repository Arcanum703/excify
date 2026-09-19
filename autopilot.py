import copy
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    amount: Decimal = Field(gt=0, le=1000000, max_digits=12, decimal_places=2)
    target_rate: Decimal = Field(gt=0, le=100, decimal_places=6)
    minimum_rate: Decimal = Field(gt=0, le=100, decimal_places=6)
    deadline: AwareDatetime
    strategy: Literal["target_only", "optimal"] = "optimal"
    request_id: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}$")

    @model_validator(mode="after")
    def validate_rates(self):
        if self.minimum_rate >= self.target_rate:
            raise ValueError("Minimum rate must be below the favorable target.")
        return self


def utcnow():
    return datetime.now(timezone.utc)


def money(value):
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def add_event(plan, code, text, now, rate=None):
    event = {"at": now.isoformat(), "code": code, "text": text, "rate": rate}
    plan["events"] = (plan["events"] + [event])[-40:]
    plan["reason"] = text


def new_plan(request, now=None):
    now = now or utcnow()
    if not now + timedelta(hours=26) <= request.deadline <= now + timedelta(days=90):
        raise ValueError("Daily autopilot needs a deadline 26 hours to 90 days from now.")
    plan = request.model_dump(mode="json")
    plan.update(id=request.request_id or uuid.uuid4().hex, amount=money(request.amount), status="active",
                created_at=now.isoformat(), last_checked_at=None, receipt=None,
                events=[], reason="Waiting for the first rate check.")
    add_event(plan, "activated", "Autopilot enabled. Simulated exchanges only. Minimum rate is never overridden.", now)
    return plan


def set_enabled(original, enabled, now=None):
    now = now or utcnow()
    plan = copy.deepcopy(original)
    if plan["status"] in ("completed", "expired"):
        raise ValueError("A finished plan cannot be restarted. Create a new plan.")
    if datetime.fromisoformat(plan["deadline"]) <= now:
        return apply_check(plan, None, now)
    status = "active" if enabled else "paused"
    if plan["status"] != status:
        plan["status"] = status
        add_event(plan, status, "Autopilot resumed." if enabled else "Autopilot paused. No exchange will execute while paused.", now)
    return plan


def valid_quote(quote, now):
    if not quote:
        return False
    try:
        age = (now.date() - date.fromisoformat(quote["date"])).days
        fetch_age = now - datetime.fromisoformat(quote["fetched_at"])
        return (0 <= age <= 4 and timedelta(0) <= fetch_age <= timedelta(minutes=30)
                and math.isfinite(float(quote["rate"])) and float(quote["rate"]) > 0)
    except (KeyError, ValueError, TypeError):
        return False


def apply_check(original, quote, now=None):
    now = now or utcnow()
    plan = copy.deepcopy(original)
    if plan["status"] in ("completed", "expired"):
        return plan
    deadline = datetime.fromisoformat(plan["deadline"])
    if now >= deadline:
        plan["status"] = "expired"
        add_event(plan, "expired", "Deadline passed without an eligible exchange. No money moved and your minimum was not overridden.", now)
        return plan
    if plan["status"] == "paused":
        return plan
    plan["last_checked_at"] = now.isoformat()
    if not valid_quote(quote, now):
        add_event(plan, "rate_unavailable", "No sufficiently fresh verified rate. Holding safely; no simulated exchange was executed.", now)
        return plan
    rate = Decimal(str(quote["rate"]))
    minimum = Decimal(plan["minimum_rate"])
    target = Decimal(plan["target_rate"])
    code, reason, execute = "waiting", "Waiting for your target or an eligible optimal opportunity.", False
    benchmark = quote.get("optimal_rate")
    if rate < minimum:
        code, reason = "below_minimum", "Rate is below your minimum. Holding, even if the deadline is close."
    elif rate >= target:
        code, reason, execute = "target_reached", "Your favorable target was reached.", True
    elif plan["strategy"] == "optimal" and deadline - now <= timedelta(hours=36):
        code, reason, execute = "deadline_window", "Deadline is within 36 hours. Optimal mode accepts this rate above your minimum.", True
    elif (plan["strategy"] == "optimal" and benchmark is not None
          and math.isfinite(float(benchmark)) and rate >= Decimal(str(benchmark))):
        code, reason, execute = "historical_opportunity", "Rate meets the upper-quartile benchmark of recent historical observations and respects your minimum.", True
    if execute:
        plan["status"] = "completed"
        plan["receipt"] = {
            "id": "SIM-" + plan["id"], "simulated": True, "executed_at": now.isoformat(),
            "sent_eur": plan["amount"], "received_usd": money(Decimal(plan["amount"]) * rate),
            "rate": str(rate), "rate_date": quote["date"], "source": quote["source"],
            "reason": reason, "fees": "Not modeled; reference-rate simulation only.",
        }
    add_event(plan, code, reason, now, float(rate))
    return plan
