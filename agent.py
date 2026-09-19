import json
import os
from pathlib import Path

from openai import AsyncOpenAI

import pattern_engine
from rates import convert, get_rates

HISTORY_PATH = Path(__file__).parent / "data" / "history.json"

# Cerebras exposes an OpenAI-compatible API, so we drive it with the OpenAI SDK.
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
MODEL = "gpt-oss-120b"
MAX_TOOL_ITERATIONS = 6

SYSTEM_PROMPT = """You are a helpful currency exchange assistant. You can:
- Convert amounts between currencies
- Look up current exchange rates
- Summarise the user's transaction history
- Answer questions about currency trends or general FX topics

When the user asks to convert currency or get rates, use the provided tools.
Always be concise and friendly."""

# OpenAI-style tool schema (Cerebras supports function calling on Llama 3.3 70B).
TOOLS = [
    {
        "type": "function",
        "function": {
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
    },
    {
        "type": "function",
        "function": {
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
    },
    {
        "type": "function",
        "function": {
            "name": "get_transaction_history",
            "description": "Return the user's past currency exchange transactions.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


def _client() -> AsyncOpenAI:
    """Build a Cerebras client, raising a clear error if the key is missing."""
    api_key = os.getenv("CEREBRAS_API_KEY")
    if not api_key:
        raise RuntimeError(
            "CEREBRAS_API_KEY is not set. Get a free key at https://cloud.cerebras.ai "
            "and set it as an environment variable (or in your .env file)."
        )
    return AsyncOpenAI(api_key=api_key, base_url=CEREBRAS_BASE_URL)


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
    return (
        f"Reference rate: {current_rate} USD per EUR. Historical comparison: {avg_rate}. "
        f"Rule-based assessment: {decision}. This is not a forecast or a real exchange. "
        "Autopilot execution is controlled by the explicitly authorized plan and its protected minimum."
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
    """Run an agentic loop against Cerebras and return the final assistant text."""
    client = _client()
    convo = [{"role": "system", "content": SYSTEM_PROMPT}] + list(messages)

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await client.chat.completions.create(
            model=MODEL,
            messages=convo,
            tools=TOOLS,
            tool_choice="auto",
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            # No tool calls — return the assistant's text answer.
            return msg.content or ""

        # Record the assistant turn (with its tool-call requests), then run them.
        convo.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            output = await _run_tool(tc.function.name, args)
            convo.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": output,
            })

    # Safety valve: ask for a final answer without further tool use.
    response = await client.chat.completions.create(model=MODEL, messages=convo)
    return response.choices[0].message.content or ""
