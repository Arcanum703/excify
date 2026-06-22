# Wise Agent

An AI-powered currency exchange agent. It watches EUR/USD rates, learns when you usually exchange money, and acts automatically when it spots a better rate.

---

## REQUIREMENTS

- Python 3.10 or higher ([download here](https://www.python.org/downloads/))
- Windows PowerShell or Mac/Linux Terminal

---

## SETUP STEPS

**Step 1: Get the project folder**

Download or copy the `wise-agent` folder to your computer. It should contain `main.py`, `agent.py`, `rates.py`, and `requirements.txt`.

**Step 2: Open PowerShell and navigate to the folder**

On Windows, open PowerShell and type:
```
cd wise-agent
```

On Mac/Linux, open Terminal and type:
```
cd wise-agent
```

**Step 3: Install dependencies**

```
pip install -r requirements.txt
```

This downloads all the Python packages the project needs. It may take a minute.

**Step 4: Set your API keys**

The easiest way is to create a file named `.env` in the project folder (copy `.env.example`) and fill in your keys:
```
CEREBRAS_API_KEY=your_key_here
ELEVENLABS_API_KEY=your_key_here
```
The app loads this file automatically on startup.

Alternatively, set them as environment variables:

On Windows (PowerShell):
```
$env:CEREBRAS_API_KEY="your_key_here"
$env:ELEVENLABS_API_KEY="your_key_here"
```

On Mac/Linux (Terminal):
```
export CEREBRAS_API_KEY="your_key_here"
export ELEVENLABS_API_KEY="your_key_here"
```

Replace `your_key_here` with your actual keys (see API KEYS section below).

**Step 5: Start the server**

```
python -m uvicorn main:app --port 8000
```

You should see a message like `Uvicorn running on http://0.0.0.0:8000`. That means it is working.

**Step 6: Check it is running**

Open your browser and go to:
```
http://localhost:8000
```

You should see the **Excify web app**. (For a plain health check, visit `http://localhost:8000/health` — it returns `{"status":"ok","service":"Wise Agent"}`.)

---

## API KEYS NEEDED

You need two API keys to run this project:

- **CEREBRAS_API_KEY** — powers the AI chat agent (Llama 3.3 70B via Cerebras)
  Get it free from: https://cloud.cerebras.ai

- **ELEVENLABS_API_KEY** — used for text-to-speech audio (optional)
  Get it from: https://elevenlabs.io/app/developers
  Without it, the voice features fall back to your browser's built-in speech.

Or ask Sami for the keys.

---

## HOW TO TEST

Open a **second** PowerShell or Terminal window (keep the server running in the first one).

**On Windows (PowerShell):**
```
Invoke-RestMethod -Uri "http://localhost:8000/deposit" -Method POST -ContentType "application/json" -Body '{"amount_eur": 1200}'
```

**On Mac/Linux (Terminal):**
```
curl -X POST http://localhost:8000/deposit -H "Content-Type: application/json" -d '{"amount_eur": 1200}'
```

---

## WHAT YOU SHOULD SEE

The response is a JSON object. Here is what each field means:

```json
{
  "status": "EXECUTED",              -- What the agent decided: EXECUTED, HOLDING, or MONITORING
  "user": "Sami",                    -- The user whose pattern was analysed
  "deposited": "EUR 1,200.00",       -- The amount you sent in euros
  "rate_today": 1.0958,              -- The EUR/USD exchange rate right now
  "rate_friday_average": 1.0938,     -- Sami's average rate, computed from history
  "rate_difference": "+1.46%",       -- How much better (or worse) today's rate is
  "profit_loss": "+$17.56 (profit)", -- Money gained by acting today instead of Friday
  "insight": "Agent acted 2 days before your usual Friday, saving you money",
  "pattern": "Exchanges every Friday between 09:00-14:00 (92% confidence)",
  "agent_reasoning": "You usually wait until Friday...", -- Full explanation from the agent
  "audio_url": "/static/audio.mp3"   -- Play this URL to hear the reasoning out loud
}
```

To play the audio, open this in your browser after calling /deposit:
```
http://localhost:8000/static/audio.mp3
```

---

## OTHER ENDPOINTS

| Method | URL | What it does |
|--------|-----|--------------|
| GET | `/` | The Excify web app (UI) |
| GET | `/health` | Health check (JSON) |
| GET | `/rate` | Current live EUR/USD rate |
| GET | `/history` | Sami's past transactions |
| GET | `/audio` | Play the last generated audio |
| POST | `/deposit` | Run the agent with an amount in EUR |
| POST | `/speak` | Text-to-speech: returns MP3 audio for given text |
| POST | `/convert` | Convert an amount between two currencies |
| POST | `/chat` | Chat with the AI agent (Cerebras) |

---

## DEPLOY (free, one service)

The app serves both the API and the web UI, so it deploys as a single service.
It's preconfigured for **Render** via `render.yaml`.

1. Push this repo to GitHub (already done if you cloned it from there).
2. Go to https://dashboard.render.com → **New** → **Blueprint**.
3. Select this repository. Render reads `render.yaml` and creates the service.
4. In the service's **Environment** tab, add your two secrets:
   - `CEREBRAS_API_KEY`
   - `ELEVENLABS_API_KEY`
5. Click **Deploy**. When it goes live, open the Render URL — that's your app.

Notes:
- The free tier sleeps after ~15 min idle; the first request then takes ~50s to wake.
- The start command binds to `$PORT` (set by the host); locally it defaults to 8000.
- A `Procfile` is also included for Railway / other Heroku-style platforms.
