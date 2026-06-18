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

On Windows (PowerShell):
```
$env:GEMINI_API_KEY="your_key_here"
$env:ELEVENLABS_API_KEY="your_key_here"
```

On Mac/Linux (Terminal):
```
export GEMINI_API_KEY="your_key_here"
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

You should see: `{"status":"ok","service":"Wise Agent"}`

---

## API KEYS NEEDED

You need two API keys to run this project:

- **GEMINI_API_KEY** — used for the AI decision logic
  Get it from: https://aistudio.google.com/app/apikey

- **ELEVENLABS_API_KEY** — used for text-to-speech audio
  Get it from: https://elevenlabs.io/app/developers

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
  "rate_friday_average": 1.08,       -- Sami's typical Friday exchange rate
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
| GET | `/` | Health check |
| GET | `/rate` | Current live EUR/USD rate |
| GET | `/history` | Sami's past transactions |
| GET | `/audio` | Play the last generated audio |
| POST | `/deposit` | Run the agent with an amount in EUR |
