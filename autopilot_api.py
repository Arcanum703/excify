import asyncio
import copy
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from autopilot import PlanInput, apply_check, new_plan, set_enabled, utcnow, valid_quote
from market import MarketUnavailable, get_market
from plan_store import get_store, update_record

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


async def optional_market():
    try:
        return await get_market()
    except MarketUnavailable:
        return None


async def save_new(key, plan):
    existing, _ = await get_store().read(key)
    if existing is None and len(await get_store().keys()) >= 100:
        raise HTTPException(429, "This free demo has reached its session capacity.")

    def append(record):
        record = copy.deepcopy(record or {"plans": []})
        if any(p["id"] == plan["id"] for p in record["plans"]):
            return record
        if len(record["plans"]) >= 10:
            raise HTTPException(429, "This demo supports ten plans per browser session.")
        record["plans"].insert(0, plan)
        return record

    return await update_record(key, append)


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
    return {"plans": (record or {}).get("plans", []), "heartbeat": heartbeat,
            "background_enabled": scheduled, "storage": "Vercel private Blob" if os.getenv("BLOB_READ_WRITE_TOKEN") else "Local SQLite",
            "schedule": "Daily, 17:00–18:00 UTC", "simulated": True}


@router.post("/plans", status_code=201)
async def create_plan(req: PlanInput, request: Request):
    key = account_key(request)
    plan = new_plan(req)
    plan = apply_check(plan, await optional_market())
    return await save_new(key, plan)


@router.patch("/plans/{plan_id}")
async def toggle_plan(plan_id: str, req: ToggleRequest, request: Request):
    key = account_key(request)
    quote = await optional_market() if req.enabled else None
    now = utcnow()
    return await modify_plan(key, plan_id, lambda p: apply_check(set_enabled(p, req.enabled, now), quote, now) if req.enabled else set_enabled(p, False, now))


@router.post("/plans/{plan_id}/check")
async def check_plan(plan_id: str, request: Request):
    key = account_key(request)
    quote = await optional_market()
    now = utcnow()

    def check(plan):
        if plan.get("last_checked_at") and now - datetime.fromisoformat(plan["last_checked_at"]) < timedelta(minutes=5):
            return plan
        return apply_check(plan, quote, now)

    return await modify_plan(key, plan_id, check)


@router.post("/exchange", status_code=201)
async def exchange(req: ExchangeRequest, request: Request):
    key = account_key(request)
    quote = await get_market()
    if not valid_quote(quote, utcnow()):
        raise HTTPException(503, "The reference rate is too old for a simulated exchange.")
    now = utcnow()
    rate = Decimal(str(quote["rate"]))
    plan = new_plan(PlanInput(amount=req.amount, target_rate=rate, minimum_rate=rate / 2,
                              deadline=now + timedelta(days=2), strategy="target_only"), now)
    plan["id"] = req.request_id
    plan = apply_check(plan, quote, now)
    return await save_new(key, plan)


@router.post("/replay")
async def replay(req: PlanInput, request: Request):
    account_key(request)
    quote = await get_market()
    observations = quote["history"]
    if len(observations) < 8:
        raise HTTPException(503, "Not enough historical observations for a replay.")
    window = observations[-7:]
    start = datetime.fromisoformat(window[0]["date"] + "T17:00:00+00:00")
    deadline = datetime.fromisoformat(window[-1]["date"] + "T23:59:00+00:00")
    plan = new_plan(req.model_copy(update={"deadline": deadline}), start)
    for point in window:
        tick = datetime.fromisoformat(point["date"] + "T17:00:00+00:00")
        cutoff = (tick.date() - timedelta(days=30)).isoformat()
        prior = sorted(p["rate"] for p in observations if cutoff <= p["date"] < point["date"])
        benchmark = prior[max(0, (len(prior) * 3 + 3) // 4 - 1)] if prior else None
        plan = apply_check(plan, {**point, "optimal_rate": benchmark, "source": quote["source"], "fetched_at": tick.isoformat()}, tick)
    if plan["status"] == "active":
        plan = apply_check(plan, None, deadline)
    return {"replay": True, "saved": False, "plan": plan, "observations": window,
            "note": "Historical replay only. Uses observations available at each step, not future rates. No live plan changed."}


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
            def check(record):
                if not record:
                    return record
                now = utcnow()
                return {**record, "plans": [apply_check(p, quote, now) for p in record["plans"]]}
            await update_record(key, check)

    results = await asyncio.gather(*(process(key) for key in keys), return_exceptions=True)
    failed = sum(isinstance(result, Exception) for result in results)
    heartbeat = {"checked_at": utcnow().isoformat(), "sessions": len(keys), "failed": failed,
                 "market_available": quote is not None, "status": "degraded" if failed or quote is None else "ok"}
    await update_record("heartbeat", lambda previous: heartbeat)
    if failed:
        raise HTTPException(503, "Some plans could not be checked. Monitoring is degraded.")
    return heartbeat
