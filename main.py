import json
import os
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent import chat
from autopilot_api import router
from market import MarketUnavailable, get_market
from plan_store import StoreConflict, StoreUnavailable
from rates import convert, get_rates

# Load API keys from a local .env file if present (CEREBRAS_API_KEY, ELEVENLABS_API_KEY).
# utf-8-sig tolerates a BOM, which Windows editors (Notepad, PowerShell) often add.
load_dotenv(encoding="utf-8-sig")
load_dotenv(Path(__file__).parent / ".env.local", encoding="utf-8-sig")

HISTORY_PATH = Path(__file__).parent / "data" / "history.json"
INDEX_PATH = Path(__file__).parent / "index.html"
STATIC_DIR = Path(__file__).parent / "static"
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"
ELEVENLABS_MODEL = "eleven_turbo_v2"

app = FastAPI(title="Excify Autopilot", description="AI-directed EUR/HUF timing with rule-enforced safety limits and simulated execution", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type", "X-Excify-Intent"])
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(router)


@app.middleware("http")
async def response_headers(request: Request, call_next):
    response = await call_next(request)
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.exception_handler(MarketUnavailable)
async def market_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


@app.exception_handler(StoreConflict)
async def conflict_error(request, exc):
    return JSONResponse(status_code=409, content={"detail": "The plan changed during this request. Refresh and try again."})


@app.exception_handler(StoreUnavailable)
@app.exception_handler(httpx.HTTPError)
async def storage_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": "Persistent storage or an external service is unavailable. No action was confirmed. Please retry."})


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    messages: list[dict] = Field(min_length=1, max_length=12)  # list of {"role": "user"|"assistant", "content": str}


class ChatResponse(BaseModel):
    response: str


class ConvertRequest(BaseModel):
    amount: float = Field(gt=0, le=1000000, allow_inf_nan=False)
    from_currency: Literal["EUR"] = "EUR"
    to_currency: Literal["HUF"] = "HUF"


class DepositRequest(BaseModel):
    amount_eur: float = Field(gt=0, le=1000000, allow_inf_nan=False)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1200)
    model: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _tts_bytes(text: str, model_id: str = ELEVENLABS_MODEL) -> bytes | None:
    """Stream optional speech without writing shared audio files."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return None
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(ELEVENLABS_URL,
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={"text": text, "model_id": ELEVENLABS_MODEL,
                      "voice_settings": {"stability": 0.5, "similarity_boost": 0.5}})
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError:
            return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    """Serve the Excify dashboard."""
    return FileResponse(INDEX_PATH, media_type="text/html")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/health")
async def health():
    """Lightweight health check for uptime monitors and platform probes."""
    return {"status": "ok", "service": "Excify", "simulated": True}


@app.get("/rate")
async def rate():
    """Return the dated EUR/HUF reference rate."""
    quote = await get_market()
    return {"from": "EUR", "to": "HUF", "rate": quote["rate"], "date": quote["date"], "source": quote["source"]}


@app.get("/history")
async def history():
    """Return the bundled sample history, not live customer transactions."""
    return {"sample": True, "transactions": json.loads(HISTORY_PATH.read_text())}


async def _reference_rates():
    """Return verified observations for the legacy quote endpoint."""
    # Current reference rate comes from the same provider as autopilot.
    quote = await get_market()
    # Historical observations are evidence, not a future forecast.
    observations = [point["rate"] for point in quote["history"]]
    # Unavailable market data is surfaced, never simulated as live data.
    return quote, min(observations), max(observations)


@app.post("/deposit")
async def deposit(req: DepositRequest):
    """Return a quote; execution requires explicit confirmation via /api/exchange."""
    quote, low, high = await _reference_rates()
    conversion = await convert(req.amount_eur, "EUR", "HUF")
    return {**conversion, "status": "QUOTED", "rate_today": quote["rate"],
            "historical_low": low, "historical_high": high,
            "agent_reasoning": "This is a reference-rate quote, not an executed transaction. Confirm a simulated exchange or activate an autopilot plan."}


@app.post("/speak")
async def speak(req: SpeakRequest):
    """Return optional speech; the UI can use browser speech without an API key."""
    if not req.text.strip():
        raise HTTPException(400, "text cannot be empty")
    audio = await _tts_bytes(req.text)
    if audio is None:
        raise HTTPException(503, "Optional text-to-speech is not configured.")
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/audio")
async def audio():
    """Shared audio files are not used by the dashboard."""
    raise HTTPException(410, "Use browser speech or the streaming /speak endpoint.")


@app.get("/rates/{base_currency}")
async def exchange_rates(base_currency: str):
    """Return reference rates for a supported base currency."""
    return {"base": base_currency.upper(), "rates": await get_rates(base_currency)}


@app.post("/convert")
async def convert_currency(req: ConvertRequest):
    """Convert an amount between supported currencies without moving money."""
    return await convert(req.amount, req.from_currency, req.to_currency)


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    """Optional Cerebras chat, separate from deterministic execution rules."""
    if not os.getenv("CEREBRAS_API_KEY") or os.getenv("VERCEL"):
        raise HTTPException(503, "Optional chat is disabled. Autonomous timing requires the configured Devin API; unavailable AI holds safely.")
    if any(m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str)
           or len(m["content"]) > 1500 for m in req.messages):
        raise HTTPException(400, "Invalid conversation messages.")
    return ChatResponse(response=await chat(req.messages))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
