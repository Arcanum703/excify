import copy
import hashlib
import hmac
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse

from fastapi import HTTPException

from autopilot import AUTHORITY, add_event, apply_check, requires_ai, safety_block, utcnow, valid_quote
from devin_timing import AIRetryableError, AIUnavailable, configuration, evidence, session_decision, start_session
from market import MarketUnavailable, get_market
from plan_store import get_store, update_record


async def optional_market():
    try:
        return await get_market()
    except MarketUnavailable:
        return None


async def read_plan(key, plan_id):
    record, _ = await get_store().read(key)
    plan = next((p for p in (record or {}).get("plans", []) if p["id"] == plan_id), None)
    if plan is None:
        raise HTTPException(404, "Plan not found in this session.")
    return record, plan


async def modify_plan(key, plan_id, change):
    def transform(record):
        if not record:
            raise HTTPException(404, "Plan not found in this session.")
        result = copy.deepcopy(record)
        for index, plan in enumerate(result["plans"]):
            if plan["id"] == plan_id:
                result["plans"][index] = change(plan)
                return result
        raise HTTPException(404, "Plan not found in this session.")
    return await update_record(key, transform)


def public_record(record):
    result = copy.deepcopy(record or {"plans": []})
    for plan in result["plans"]:
        plan.pop("last_ai_delivery", None)
        pending = plan.get("pending_ai")
        if pending:
            plan["pending_ai"] = {key: pending.get(key) for key in ("id", "created_at", "expires_at", "session_id", "retry_at")}
    return result


def callback_base():
    if os.getenv("VERCEL_ENV") != "production":
        return None
    value = os.getenv("EXCIFY_PUBLIC_URL", "").rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.username or parsed.password:
        raise AIUnavailable("Production AI delivery is not configured. Holding safely.")
    return value


async def reserve_budget(attempt_id):
    key = "ai-budget-" + utcnow().date().isoformat()
    limit = max(1, min(int(os.getenv("DEVIN_MAX_SESSIONS_PER_DAY", "20")), 100))

    def reserve(record):
        record = copy.deepcopy(record or {"attempts": []})
        if attempt_id not in record["attempts"]:
            if len(record["attempts"]) >= limit:
                raise AIUnavailable("Daily AI session safety budget reached. Holding; no rule-based fallback.")
            record["attempts"].append(attempt_id)
        return record
    await update_record(key, reserve)


async def fail_attempt(key, plan_id, attempt_id, message):
    def fail(plan):
        pending = plan.get("pending_ai")
        if not pending or pending["id"] != attempt_id:
            return plan
        now = utcnow()
        result = apply_check(plan, pending["quote"], now, error=message, allow_target=False)
        result["pending_ai"] = None
        add_event(result, "ai_error", message, now, pending["quote"].get("rate"),
                  rate_date=pending["quote"].get("date"), pair="EUR/HUF", action="hold",
                  authority=AUTHORITY, ai_action=None, ai_reasoning=None, context_id=attempt_id,
                  session_id=pending.get("session_id"))
        return result
    return await modify_plan(key, plan_id, fail)


async def record_poll_retry(key, plan_id, attempt_id, message=None):
    def update(plan):
        pending = plan.get("pending_ai")
        if not pending or pending["id"] != attempt_id:
            return plan
        result = copy.deepcopy(plan)
        job = result["pending_ai"]
        now = utcnow()
        if message:
            job["poll_failures"] = job.get("poll_failures", 0) + 1
            delay = min(30 * 2 ** min(job["poll_failures"] - 1, 4), 300)
            job["retry_at"] = min(now + timedelta(seconds=delay), datetime.fromisoformat(job["expires_at"])).isoformat()
            text = message + " The existing analysis and authenticated callback remain active; polling will retry."
            code = "ai_retry"
        else:
            job.pop("poll_failures", None)
            job.pop("retry_at", None)
            text, code = "Connection restored. Devin is still analyzing; no timing decision yet.", "ai_poll_recovered"
        add_event(result, code, text, now, authority=AUTHORITY, context_id=attempt_id,
                  session_id=job.get("session_id"), retry_at=job.get("retry_at"))
        return result
    return await modify_plan(key, plan_id, update)


def in_cooldown(plan, now):
    last_requested = plan.get("last_requested_at")
    return bool(last_requested and now - datetime.fromisoformat(last_requested) < timedelta(minutes=5))


async def finalize_decision(key, plan_id, attempt_id, decision):
    quote = await optional_market()

    def finish(plan):
        pending = plan.get("pending_ai")
        if not pending or pending["id"] != attempt_id:
            return plan
        now = utcnow()
        context = pending["context"]
        invalid = (decision.context_id != attempt_id or decision.rate_date != context["rate_date"]
                   or decision.rate != context["current_rate"]
                   or now > datetime.fromisoformat(pending["expires_at"])
                   or plan.get("generation", 0) != pending["generation"])
        if invalid:
            result = apply_check(plan, quote, now, error="AI response no longer matches the authorized plan or analysis window. Holding safely.", allow_target=False)
            add_event(result, "ai_rejected", "AI response was rejected: changed plan, expired analysis, or mismatched evidence.", now,
                      quote.get("rate") if quote else None, rate_date=quote.get("date") if quote else None,
                      pair="EUR/HUF", action="hold", ai_action=decision.action, ai_reasoning=decision.reasoning,
                      authority=AUTHORITY, session_id=pending.get("session_id"), context_id=attempt_id, safety_allowed=False)
        else:
            result = apply_check(plan, quote, now, decision=decision, session_id=pending.get("session_id"))
            result["events"][-1]["evidence"] = {k: context[k] for k in (
                "current_rate", "rate_date", "trend", "favorable_target", "protected_minimum", "deadline", "strategy")}
        result["last_ai_delivery"] = {"id": attempt_id, "token_hash": pending["token_hash"]}
        result["pending_ai"] = None
        return result

    return await modify_plan(key, plan_id, finish)


async def receive_decision(key, plan_id, attempt_id, decision, authorization):
    if not re.fullmatch(r"account-[a-f0-9]{64}", key):
        raise HTTPException(404, "Unknown decision.")
    record, plan = await read_plan(key, plan_id)
    pending = plan.get("pending_ai")
    delivery = pending if pending and pending["id"] == attempt_id else plan.get("last_ai_delivery")
    token = authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
    digest = hashlib.sha256(token.encode()).hexdigest()
    if not token or not delivery or delivery["id"] != attempt_id or not hmac.compare_digest(digest, delivery["token_hash"]):
        raise HTTPException(401, "Unauthorized decision delivery.")
    if not pending or pending["id"] != attempt_id:
        return record
    if decision.context_id != attempt_id:
        raise HTTPException(422, "Decision context does not match this request.")
    return await finalize_decision(key, plan_id, attempt_id, decision)


async def check_plan(key, plan_id):
    record, plan = await read_plan(key, plan_id)
    if plan["status"] in ("completed", "expired"):
        return record
    quote = await optional_market()
    now = utcnow()
    if safety_block(plan, quote, now) is None and Decimal(str(quote["rate"])) >= Decimal(plan["target_rate"]):
        return await modify_plan(key, plan_id, lambda current: apply_check(current, quote, utcnow()))
    pending = plan.get("pending_ai")
    if pending and now > datetime.fromisoformat(pending["expires_at"]):
        record = await fail_attempt(key, plan_id, pending["id"], "The AI analysis window expired. No exchange was authorized.")
        plan = next(p for p in record["plans"] if p["id"] == plan_id)
        pending = plan.get("pending_ai")
        if plan["status"] in ("completed", "expired"):
            return record
    if pending:
        if pending.get("retry_at") and now < datetime.fromisoformat(pending["retry_at"]):
            return record
        if pending.get("session_id"):
            try:
                decision = await session_decision(pending["session_id"])
                if decision:
                    return await finalize_decision(key, plan_id, pending["id"], decision)
                if pending.get("retry_at"):
                    return await record_poll_retry(key, plan_id, pending["id"])
            except AIRetryableError as exc:
                return await record_poll_retry(key, plan_id, pending["id"], str(exc))
            except AIUnavailable as exc:
                return await fail_attempt(key, plan_id, pending["id"], str(exc))
        return record
    if in_cooldown(plan, now):
        return record
    quote = await optional_market()
    now = utcnow()
    if not requires_ai(plan, quote, now):
        return await modify_plan(key, plan_id, lambda current: apply_check(current, quote, utcnow()))
    try:
        configuration()
        base = callback_base()
    except AIUnavailable as exc:
        message = str(exc)
        return await modify_plan(key, plan_id, lambda current: apply_check(current, quote, utcnow(), error=message))
    attempt_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    context = evidence(plan, quote, attempt_id, now)
    job = {"id": attempt_id, "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=30)).isoformat(),
           "generation": plan.get("generation", 0), "session_id": None,
           "token_hash": hashlib.sha256(token.encode()).hexdigest(), "context": context, "quote": quote}

    def claim(current):
        claim_now = utcnow()
        if (current["status"] != "active" or current.get("pending_ai")
                or current.get("generation", 0) != job["generation"] or in_cooldown(current, claim_now)):
            return current
        if not requires_ai(current, quote, claim_now):
            return apply_check(current, quote, claim_now)
        result = copy.deepcopy(current)
        result["pending_ai"] = job
        result["last_requested_at"] = now.isoformat()
        add_event(result, "ai_pending", "Devin is deciding execute or wait from the real rate, trend, and your goal. No exchange is authorized yet.", now,
                  quote["rate"], rate_date=quote["date"], pair="EUR/HUF", authority=AUTHORITY, context_id=attempt_id)
        return result

    record = await modify_plan(key, plan_id, claim)
    current = next(p for p in record["plans"] if p["id"] == plan_id)
    if (current.get("pending_ai") or {}).get("id") != attempt_id:
        return record
    try:
        await reserve_budget(attempt_id)
        callback = f"{base}/api/ai-decisions/{key}/{plan_id}/{attempt_id}" if base else None
        session = await start_session(context, callback, token if callback else None)
    except AIUnavailable as exc:
        return await fail_attempt(key, plan_id, attempt_id, str(exc))

    def bind(current):
        result = copy.deepcopy(current)
        if (result.get("pending_ai") or {}).get("id") == attempt_id:
            result["pending_ai"].update(session)
        for event in result["events"]:
            if event.get("context_id") == attempt_id:
                event["session_id"] = session["session_id"]
        if result.get("receipt") and result["events"][-1].get("context_id") == attempt_id:
            result["receipt"]["session_id"] = session["session_id"]
        return result
    return await modify_plan(key, plan_id, bind)
