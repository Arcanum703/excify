import json
import os
import random
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import pattern_engine
from agent import analyze_pattern, chat, generate_reasoning
from rates import convert, get_current_rate, get_rates

# Load API keys from a local .env file if present (CEREBRAS_API_KEY, ELEVENLABS_API_KEY).
# utf-8-sig tolerates a BOM, which Windows editors (Notepad, PowerShell) often add.
load_dotenv(encoding="utf-8-sig")

HISTORY_PATH = Path(__file__).parent / "data" / "history.json"
INDEX_PATH = Path(__file__).parent / "index.html"
STATIC_DIR = Path(__file__).parent / "static"
AUDIO_PATH = STATIC_DIR / "audio.mp3"
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"
ELEVENLABS_MODEL = "eleven_turbo_v2"
AVG_RATE_BASELINE = 1.08

STATIC_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Wise Agent", description="AI-powered currency exchange assistant", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    messages: list[dict]  # list of {"role": "user"|"assistant", "content": str}


class ChatResponse(BaseModel):
    response: str


class ConvertRequest(BaseModel):
    amount: float
    from_currency: str
    to_currency: str


class DepositRequest(BaseModel):
    amount_eur: float


class SpeakRequest(BaseModel):
    text: str
    model: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _tts_bytes(text: str, model_id: str = ELEVENLABS_MODEL) -> bytes | None:
    """Synthesise speech with ElevenLabs and return raw MP3 bytes.
    Returns None if no API key is set or the call fails."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        return None
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                ELEVENLABS_URL,
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json={
                    "text": text,
                    "model_id": model_id,
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.5},
                },
            )
            resp.raise_for_status()
            return resp.content
        except Exception:
            return None


async def _speak(text: str) -> bool:
    """Generate TTS audio and save it to static/audio.mp3.
    Returns True on success, False if TTS is unavailable."""
    audio = await _tts_bytes(text)
    if audio is None:
        return False
    AUDIO_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUDIO_PATH.write_bytes(audio)
    return True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    """Serve the web UI. Falls back to a JSON health response if the file is missing."""
    if INDEX_PATH.exists():
        return FileResponse(INDEX_PATH, media_type="text/html")
    return {"status": "ok", "service": "Wise Agent"}


@app.get("/health")
async def health():
    """Lightweight health check for uptime monitors and platform probes."""
    return {"status": "ok", "service": "Wise Agent"}


@app.get("/rate")
async def rate():
    """Return the current live EUR/USD rate."""
    try:
        current = await get_current_rate()
        return {"from": "EUR", "to": "USD", "rate": current}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/history")
async def history():
    """Return the full transaction history."""
    try:
        return json.loads(HISTORY_PATH.read_text())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _simulate_rates() -> tuple[float, float, float]:
    """Return (current_rate, rate_low, rate_high) for EUR/USD scenarios."""
    roll = random.random()
    if roll < 0.60:
        # 60%: execute — current rate between 1.09 and 1.105
        current_rate = round(random.uniform(1.09, 1.105), 4)
    elif roll < 0.85:
        # 25%: hold — current rate between 1.055 and 1.075
        current_rate = round(random.uniform(1.055, 1.075), 4)
    else:
        # 15%: wait — volatile, random between 1.04 and 1.12
        current_rate = round(random.uniform(1.04, 1.12), 4)
    rate_low = round(min(current_rate, AVG_RATE_BASELINE) - random.uniform(0.005, 0.01), 4)
    rate_high = round(max(current_rate, AVG_RATE_BASELINE) + random.uniform(0.005, 0.01), 4)
    return current_rate, rate_low, rate_high


@app.post("/deposit")
async def deposit(req: DepositRequest):
    """Analyse a prospective deposit and recommend execute / hold / wait."""
    try:
        tx_history = json.loads(HISTORY_PATH.read_text())
        pattern = await analyze_pattern(tx_history)
        current_rate, rate_low, rate_high = _simulate_rates()

        pe_result = pattern_engine.should_execute_now(current_rate, tx_history)
        decision = pe_result["decision"]
        avg_rate = pe_result["avg_rate"]
        rate_diff_pct = pe_result["rate_diff_pct"]

        if decision == "execute":
            savings = round((current_rate - avg_rate) * req.amount_eur, 2)
            avoided_loss = 0.0
            profit_loss_fmt = f"+${savings:,.2f} (profit)"
            status = "EXECUTED"
        elif decision == "hold":
            savings = 0.0
            raw_gap = round((avg_rate - current_rate) * req.amount_eur, 2)
            if raw_gap > 0:
                avoided_loss = raw_gap
                profit_loss_fmt = f"-${avoided_loss:,.2f} (below avg rate)"
            else:
                avoided_loss = 0.0
                profit_loss_fmt = f"+${abs(raw_gap):,.2f} above avg — waiting for execute threshold"
            status = "HOLDING"
        else:
            savings = 0.0
            avoided_loss = 0.0
            profit_loss_fmt = "volatile - monitoring"
            status = "MONITORING"

        reasoning = await generate_reasoning(
            decision, current_rate, avg_rate, pattern,
            savings=savings,
            avoided_loss=avoided_loss,
            rate_low=rate_low,
            rate_high=rate_high,
        )

        await _speak(reasoning)

        sign = "+" if rate_diff_pct >= 0 else ""
        return {
            "status": status,
            "user": "Sami",
            "deposited": f"EUR {req.amount_eur:,.2f}",
            "rate_today": current_rate,
            "rate_friday_average": avg_rate,
            "rate_difference": f"{sign}{rate_diff_pct:.2f}%",
            "profit_loss": profit_loss_fmt,
            "insight": {
                "EXECUTED": "Agent acted 2 days before your usual Friday, saving you money",
                "HOLDING": "Agent is waiting for a better rate before Friday",
                "MONITORING": "Agent is watching volatile markets, will act before Friday",
            }[status],
            "pattern": f"Exchanges every Friday between {pattern['window']} ({pattern['confidence']}% confidence)",
            "agent_reasoning": reasoning,
            "audio_url": "/static/audio.mp3",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/speak")
async def speak(req: SpeakRequest):
    """Synthesise speech from text and stream back MP3 audio.

    Returns 503 if no ELEVENLABS_API_KEY is configured; the frontend then
    falls back to the browser's built-in speech synthesis."""
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text cannot be empty")
    audio_bytes = await _tts_bytes(req.text, req.model or ELEVENLABS_MODEL)
    if audio_bytes is None:
        raise HTTPException(
            status_code=503,
            detail="Text-to-speech unavailable. Set ELEVENLABS_API_KEY to enable it.",
        )
    return Response(content=audio_bytes, media_type="audio/mpeg")


@app.get("/audio")
async def audio():
    """Return the last generated TTS audio file with browser-playable headers."""
    if not AUDIO_PATH.exists():
        raise HTTPException(status_code=404, detail="No audio file found. Call /deposit first.")
    return FileResponse(
        AUDIO_PATH,
        media_type="audio/mpeg",
        filename="audio.mp3",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-cache"},
    )


@app.get("/rates/{base_currency}")
async def exchange_rates(base_currency: str):
    """Return live exchange rates for a given base currency."""
    try:
        rates = await get_rates(base_currency)
        return {"base": base_currency.upper(), "rates": rates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/convert")
async def convert_currency(req: ConvertRequest):
    """Convert an amount between two currencies."""
    try:
        result = await convert(req.amount, req.from_currency, req.to_currency)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    """Send a conversation to the AI agent and get a response."""
    if not req.messages:
        raise HTTPException(status_code=400, detail="messages list cannot be empty")
    try:
        reply = await chat(req.messages)
        return ChatResponse(response=reply)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
