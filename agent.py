import json
import os
from pathlib import Path

import google.generativeai as genai

import pattern_engine
from rates import convert, get_rates

HISTORY_PATH = Path(__file__).parent / "data" / "history.json"

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
MODEL = "gemini-2.0-flash"

SYSTEM_PROMPT = """You are a helpful currency exchange assistant. You can:
- Convert amounts between currencies
- Look up current exchange rates
- Summarise the user's transaction history
- Answer questions about currency trends or general FX topics

When the user asks to convert currency or get rates, use the provided tools.
Always be concise and friendly."""

TOOLS = [
    {
        "name": "convert_currency",
        "description": "Convert an amount from one currency to another using live rates.",
        "parameters": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "The amount to convert."},
                "from_currency": {"type": "string", "description": "ISO 4217 source currency code (e.g. USD)."},
                "to_currency": {"type": "string", "description": "ISO 4217 target currency code (e.g. EUR)."},
            },
            "required": ["amount", "from_currency", "to_currency"],
        },
    },
    {
        "name": "get_exchange_rates",
        "description": "Retrieve current exchange rates for a base currency.",
        "parameters": {
            "type": "object",
            "properties": {
                "base_currency": {"type": "string", "description": "ISO 4217 base currency code (e.g. USD)."},
            },
            "required": ["base_currency"],
        },
    },
    {
        "name": "get_transaction_history",
        "description": "Return the user's past currency exchange transactions.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


def _to_gemini_messages(messages: list[dict]) -> list[dict]:
    """Convert {role, content} dicts to Gemini's {role, parts} format."""
    result = []
    for msg in messages:
        role = "model" if msg["role"] == "assistant" else msg["role"]
        content = msg["content"]
        if isinstance(content, str):
            result.append({"role": role, "parts": [{"text": content}]})
        else:
            # Already in Gemini parts format (tool results injected internally)
            result.append({"role": role, "parts": content})
    return result


async def analyze_pattern(history: list) -> dict:
    result = pattern_engine.analyze_exchange_pattern(history)
    # Aliases for callers that expect the old keys
    result["window"] = result["usual_window"]
    result["confidence"] = result["overall_confidence"]
    return result


async def generate_reasoning(
    decision: str,
    current_rate: float,
    avg_rate: float,
    pattern: dict,
    savings: float,
    avoided_loss: float = 0.0,
    rate_low: float = 0.0,
    rate_high: float = 0.0,
) -> str:
    if decision == "execute":
        return (
            f"You usually wait until Friday to exchange. But today is Wednesday "
            f"and the rate is {current_rate} USD per EUR - better than your typical Friday "
            f"average of {avg_rate}. I executed 2 days early. You saved ${savings:.2f} by not waiting."
        )
    if decision == "hold":
        return (
            f"Today is Wednesday. Current rate is {current_rate} USD per EUR - below "
            f"your Friday average of {avg_rate}. I am holding. Friday may bring a better rate. "
            f"Executing now would cost you ${avoided_loss:.2f} extra."
        )
    return (
        f"Rates are swinging unusually this week between {rate_low} and {rate_high} USD per EUR. "
        f"I am monitoring closely and will execute before Friday if a good window appears."
    )


async def _run_tool(name: str, args: dict) -> str:
    if name == "convert_currency":
        result = await convert(
            amount=args["amount"],
            from_currency=args["from_currency"],
            to_currency=args["to_currency"],
        )
        return json.dumps(result)

    if name == "get_exchange_rates":
        rates = await get_rates(args["base_currency"])
        return json.dumps({"base": args["base_currency"].upper(), "rates": rates})

    if name == "get_transaction_history":
        history = json.loads(HISTORY_PATH.read_text())
        return json.dumps(history)

    return json.dumps({"error": f"Unknown tool: {name}"})


async def chat(messages: list[dict]) -> str:
    """Run an agentic loop and return the final assistant text response."""
    model = genai.GenerativeModel(
        model_name=MODEL,
        system_instruction=SYSTEM_PROMPT,
        tools=TOOLS,
    )

    gemini_messages = _to_gemini_messages(messages)

    while True:
        response = await model.generate_content_async(gemini_messages)
        candidate = response.candidates[0]
        parts = candidate.content.parts

        # Check if any part is a function call
        function_calls = [p for p in parts if hasattr(p, "function_call") and p.function_call.name]

        if not function_calls:
            # No tool calls — collect text and return
            return "\n".join(p.text for p in parts if hasattr(p, "text") and p.text)

        # Append the model turn
        gemini_messages.append({"role": "model", "parts": parts})

        # Execute tools and collect function responses
        tool_response_parts = []
        for part in function_calls:
            fc = part.function_call
            output = await _run_tool(fc.name, dict(fc.args))
            tool_response_parts.append({
                "function_response": {
                    "name": fc.name,
                    "response": {"result": output},
                }
            })

        gemini_messages.append({"role": "user", "parts": tool_response_parts})
