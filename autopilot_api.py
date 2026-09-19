import asyncio
import copy
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from autopilot import AIDecision, AUTHORITY, PAIR, PlanInput, manual_exchange, money, new_plan, safety_block, set_enabled, utcnow, valid_quote
from devin_timing import configured
from market import get_market
from plan_store import get_store, update_record
from timing_service import check_plan as run_check, modify_plan, optional_market, public_record, receive_decision

router = APIRouter(prefix="/api")
COOKIE = "excify_session"


class ToggleRequest(BaseModel):
    enabled: bool


class ExchangeRequest(BaseModel):
    amount: Decimal = Field(gt=0, le=1000000, decimal_places=2, allow_inf_nan=False)
    confirmation: Literal[True]
    request_id: str = Field(pattern=r"^[a-f0-9-]{36}$")


def account_key(request):
    session = request.cookies.get(COOKIE, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", session):
        raise HTTPException(401, "Reload the page to start a private demo session.")
    if request.method not in ("GET", "HEAD") and request.headers.get("x-excify-intent") != "plan":
        raise HTTPException(403, "Missing request confirmation header.")
    return "account-" + hashlib.sha256(session.encode()).hexdigest()


def creation_intent(data, operation=None):
    if operation is None:
        operation = "exchange" if (data.get("receipt") or {}).get("authority") == "User-confirmed simulation" else "plan"
    intent = {"operation": operation, "pair": data.get("pair", PAIR), "amount": money(data["amount"])}
    if operation == "plan":
        deadline = data["deadline"]
        if isinstance(deadline, str):
            deadline = datetime.fromisoformat(deadline)
        intent.update(target_rate=Decimal(str(data["target_rate"])), minimum_rate=Decimal(str(data["minimum_rate"])),
                      deadline=deadline.astimezone(timezone.utc), strategy=data["strategy"])
    return intent


def matching_request(record, request_id, intent):
    existing = next((plan for plan in (record or {}).get("plans", []) if plan["id"] == request_id), None)
    if existing is None:
        return None
    if creation_intent(existing) != intent:
        raise HTTPException(409, "This request ID was already used with different details. Use a new request ID for a new action.")
    return record


async def existing_request(key, req, operation):
    if not req.request_id:
        return None
    record, _ = await get_store().read(key)
    return matching_request(record, req.request_id, creation_intent(req.model_dump(), operation))


async def save_new(key, plan):
    intent = creation_intent(plan)
    existing, _ = await get_store().read(key)
    if existing is None and len(await get_store().keys()) >= 100:
        raise HTTPException(429, "This demo has reached its session capacity.")

    def append(record):
        record = copy.deepcopy(record or {"plans": []})
        if matching_request(record, plan["id"], intent) is not None:
            return record
        if len(record["plans"]) >= 10:
            raise HTTPException(429, "This demo supports ten plans per browser session.")
        record["plans"].insert(0, plan)
        return record
    return await update_record(key, append)


@router.get("/market")
async def market():
    data = await get_market()
    data["usable"] = valid_quote(data, utcnow())
    return data


@router.get("/state")
async def state(request: Request, response: Response):
    session = request.cookies.get(COOKIE, "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", session):
        session = secrets.token_urlsafe(32)
        response.set_cookie(COOKIE, session, httponly=True, secure=bool(os.getenv("VERCEL")),
                            samesite="strict", max_age=365 * 86400)
    key = "account-" + hashlib.sha256(session.encode()).hexdigest()
    store = get_store()
    record, _ = await store.read(key)
    heartbeat, _ = await store.read("heartbeat")
    scheduled = bool(os.getenv("CRON_SECRET") and os.getenv("BLOB_READ_WRITE_TOKEN") and os.getenv("VERCEL_ENV") == "production")
    return {**public_record(record), "heartbeat": heartbeat, "pair": PAIR, "authority": AUTHORITY,
            "ai_configured": configured(), "background_enabled": scheduled,
            "storage": "Vercel private Blob" if os.getenv("BLOB_READ_WRITE_TOKEN") else "Local SQLite",
            "schedule": "Daily, 17:00–18:00 UTC; AI decisions arrive asynchronously", "simulated": True}


@router.post("/plans", status_code=201)
async def create_plan(req: PlanInput, request: Request):
    key = account_key(request)
    existing = await existing_request(key, req, "plan")
    if existing is not None:
        return public_record(existing)
    plan = new_plan(req)
    await save_new(key, plan)
    return public_record(await run_check(key, plan["id"]))


@router.patch("/plans/{plan_id}")
async def toggle_plan(plan_id: str, req: ToggleRequest, request: Request):
    key = account_key(request)
    record = await modify_plan(key, plan_id, lambda plan: set_enabled(plan, req.enabled))
    if req.enabled:
        record = await run_check(key, plan_id)
    return public_record(record)


@router.post("/plans/{plan_id}/check")
async def check_plan(plan_id: str, request: Request):
    return public_record(await run_check(account_key(request), plan_id))


@router.post("/ai-decisions/{key}/{plan_id}/{attempt_id}")
async def ai_callback(key: str, plan_id: str, attempt_id: str, decision: AIDecision, request: Request):
    record = await receive_decision(key, plan_id, attempt_id, decision, request.headers.get("authorization", ""))
    plan = next(p for p in record["plans"] if p["id"] == plan_id)
    return {"status": plan["status"], "reason": plan["reason"], "authority": AUTHORITY, "simulated": True}


@router.post("/exchange", status_code=201)
async def exchange(req: ExchangeRequest, request: Request):
    key = account_key(request)
    existing = await existing_request(key, req, "exchange")
    if existing is not None:
        return public_record(existing)
    quote = await get_market()
    return public_record(await save_new(key, manual_exchange(req, quote)))


@router.post("/replay")
async def replay(req: PlanInput, request: Request):
    account_key(request)
    quote = await get_market()
    events = []
    for point in quote["history"][-7:]:
        tick = datetime.fromisoformat(point["date"] + "T17:00:00+00:00")
        historical_quote = {**quote, **point, "fetched_at": tick.isoformat()}
        historical_plan = {**req.model_dump(mode="json"), "status": "active"}
        block = safety_block(historical_plan, historical_quote, tick)
        events.append({"at": tick.isoformat(), "rate": point["rate"], "rate_date": point["date"],
            "code": block[0] if block else "eligible", "text": block[1] if block else "Safety permits AI consideration. No historical AI decision was requested or fabricated.",
            "pair": PAIR, "authority": "Historical safety inspection", "action": None})
    return {"replay": True, "saved": False, "plan": {"status": "inspection", "events": events, "receipt": None,
            "reason": "Real historical rates checked against your hard limits. This is not an AI timing backtest."}}


@router.get("/cron")
async def cron(request: Request):
    secret = os.getenv("CRON_SECRET", "")
    if not secret or not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + secret):
        raise HTTPException(401, "Unauthorized")
    quote = await optional_market()
    keys = await get_store().keys()
    semaphore = asyncio.Semaphore(5)

    async def process(key):
        async with semaphore:
            record, _ = await get_store().read(key)
            for plan in (record or {}).get("plans", []):
                if plan["status"] in ("active", "paused"):
                    record = await run_check(key, plan["id"])
            return record

    results = await asyncio.gather(*(process(key) for key in keys), return_exceptions=True)
    failed = sum(isinstance(result, Exception) for result in results)
    pending = sum(bool(plan.get("pending_ai")) for record in results if isinstance(record, dict) for plan in record["plans"])
    ai_errors = sum(plan["events"][-1]["code"] in ("ai_error", "ai_unavailable")
                    for record in results if isinstance(record, dict) for plan in record["plans"] if plan["events"])
    heartbeat = {"checked_at": utcnow().isoformat(), "sessions": len(keys), "failed": failed, "pending_decisions": pending,
                 "ai_errors": ai_errors, "market_available": quote is not None, "ai_configured": configured(),
                 "status": "degraded" if failed or ai_errors or quote is None or not configured() else "ok"}
    await update_record("heartbeat", lambda previous: heartbeat)
    if failed:
        raise HTTPException(503, "Some plans could not be checked. Monitoring is degraded.")
    return heartbeat
