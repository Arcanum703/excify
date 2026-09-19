import copy
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

AUTHORITY = "AI-directed timing, rule-enforced safety limits"
PAIR = "EUR/HUF"


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    amount: Decimal = Field(gt=0, le=1000000, max_digits=12, decimal_places=2)
    target_rate: Decimal = Field(gt=0, le=100000, decimal_places=6)
    minimum_rate: Decimal = Field(gt=0, le=100000, decimal_places=6)
    deadline: AwareDatetime
    strategy: Literal["target_only", "optimal"] = "optimal"
    request_id: str | None = Field(default=None, pattern=r"^[a-f0-9-]{36}$")

    @model_validator(mode="after")
    def validate_rates(self):
        if self.minimum_rate >= self.target_rate:
            raise ValueError("Minimum rate must be below the favorable target.")
        return self


class AIDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    final: Literal[True]
    context_id: str = Field(min_length=1, max_length=64)
    pair: Literal["EUR/HUF"]
    action: Literal["execute", "wait"]
    rate: float = Field(gt=0, le=100000)
    rate_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    reasoning: str = Field(min_length=20, max_length=2000)


def utcnow():
    return datetime.now(timezone.utc)


def money(value):
    return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def add_event(plan, code, text, now, rate=None, **details):
    event = {"at": now.isoformat(), "code": code, "text": text, "rate": rate, **details}
    plan["events"] = (plan["events"] + [event])[-240:]
    plan["reason"] = text


def new_plan(request, now=None):
    now = now or utcnow()
    if not now + timedelta(hours=26) <= request.deadline <= now + timedelta(days=90):
        raise ValueError("Daily autopilot needs a deadline 26 hours to 90 days from now.")
    plan = request.model_dump(mode="json")
    plan.update(id=request.request_id or uuid.uuid4().hex, amount=money(request.amount), pair=PAIR,
                status="active", created_at=now.isoformat(), last_checked_at=None, receipt=None,
                events=[], generation=0, pending_ai=None, reason="Waiting for Devin's first timing decision.")
    add_event(plan, "activated", AUTHORITY + ". Simulated exchanges only. No rule automatically authorizes execution.", now)
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
        plan["generation"] = plan.get("generation", 0) + 1
        plan["status"] = status
        if enabled:
            plan["pending_ai"] = None
            plan["last_requested_at"] = None
        add_event(plan, status, "Autopilot resumed; a new AI decision is required." if enabled else "Autopilot paused. In-flight AI decisions cannot execute.", now)
    return plan


def valid_quote(quote, now):
    if not quote:
        return False
    try:
        age = (now.date() - date.fromisoformat(quote["date"])).days
        fetch_age = now - datetime.fromisoformat(quote["fetched_at"])
        return (quote.get("base") == "EUR" and quote.get("quote") == "HUF"
                and 0 <= age <= 4 and timedelta(0) <= fetch_age <= timedelta(minutes=30)
                and math.isfinite(float(quote["rate"])) and float(quote["rate"]) > 0)
    except (KeyError, ValueError, TypeError):
        return False


def safety_block(plan, quote, now):
    if datetime.fromisoformat(plan["deadline"]) <= now:
        return "expired", "Deadline passed. No late exchange is allowed."
    if plan["status"] == "paused":
        return "paused", "The plan is paused. Execution is blocked."
    if not valid_quote(quote, now):
        return "rate_unavailable", "No fresh verified EUR/HUF rate. Execution is blocked."
    rate = Decimal(str(quote["rate"]))
    if rate < Decimal(plan["minimum_rate"]):
        return "below_minimum", "The rate is below your protected minimum. Execution is blocked, even near the deadline."
    if plan["strategy"] == "target_only" and rate < Decimal(plan["target_rate"]):
        return "below_target", "Target-only mode requires the favorable target. Execution is blocked below it."
    return None


def apply_check(original, quote, now=None, *, decision=None, session_id=None, error=None):
    now = now or utcnow()
    plan = copy.deepcopy(original)
    if plan["status"] in ("completed", "expired"):
        return plan
    block = safety_block(plan, quote, now)
    if plan["status"] == "paused" and decision is None and (not block or block[0] != "expired"):
        return plan
    plan["last_checked_at"] = now.isoformat()
    if decision is not None and not isinstance(decision, AIDecision):
        decision = AIDecision.model_validate(decision)
    evidence_matches = decision is not None and quote is not None and (
        decision.rate_date == quote["date"] and Decimal(str(decision.rate)) == Decimal(str(quote["rate"])))
    if block:
        code, reason = block
        if code == "expired":
            plan["status"] = "expired"
    elif decision is None:
        code, reason = "ai_unavailable", error or "No valid AI decision is available. Holding; no automatic rule fallback."
    elif not evidence_matches:
        code, reason = "evidence_changed", "The market observation changed after analysis. A new AI decision is required."
    elif decision.action == "wait":
        code, reason = "ai_wait", "Devin chose to wait. " + decision.reasoning
    else:
        code, reason = "ai_execute", "Devin chose to execute within your safety limits. " + decision.reasoning
        plan["status"] = "completed"
        plan["receipt"] = {
            "id": "SIM-" + plan["id"], "simulated": True, "pair": PAIR, "executed_at": now.isoformat(),
            "sent_eur": plan["amount"], "received_huf": money(Decimal(plan["amount"]) * Decimal(str(quote["rate"]))),
            "rate": str(quote["rate"]), "rate_date": quote["date"], "source": quote["source"],
            "reason": decision.reasoning, "authority": AUTHORITY, "session_id": session_id,
            "fees": "Not modeled; reference-rate simulation only.",
        }
    add_event(plan, code, reason, now, quote.get("rate") if quote else None,
              rate_date=quote.get("date") if quote else None, pair=PAIR,
              action="execute" if code == "ai_execute" else "hold", authority=AUTHORITY,
              ai_action=decision.action if decision else None,
              ai_reasoning=decision.reasoning if decision else None,
              context_id=decision.context_id if decision else None, session_id=session_id,
              safety_allowed=block is None and evidence_matches,
              target_rate=plan["target_rate"], minimum_rate=plan["minimum_rate"], deadline=plan["deadline"])
    return plan


def manual_exchange(request, quote, now=None):
    now = now or utcnow()
    if not valid_quote(quote, now):
        raise ValueError("A fresh verified EUR/HUF reference rate is required.")
    rate = Decimal(str(quote["rate"]))
    plan = new_plan(PlanInput(amount=request.amount, target_rate=rate,
        minimum_rate=(rate / 2).quantize(Decimal("0.000001")), deadline=now + timedelta(days=2),
        strategy="target_only", request_id=request.request_id), now)
    plan["status"] = "completed"
    plan["last_checked_at"] = now.isoformat()
    plan["receipt"] = {"id": "SIM-" + plan["id"], "simulated": True, "pair": PAIR,
        "executed_at": now.isoformat(), "sent_eur": plan["amount"], "received_huf": money(request.amount * rate),
        "rate": str(rate), "rate_date": quote["date"], "source": quote["source"],
        "reason": "Explicit user confirmation, not an autonomous AI decision.", "authority": "User-confirmed simulation",
        "fees": "Not modeled; reference-rate simulation only."}
    add_event(plan, "manual_execute", "User confirmed a simulated exchange.", now, float(rate),
              rate_date=quote["date"], pair=PAIR, action="execute", authority="User-confirmed simulation")
    return plan
