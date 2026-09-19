import json
from pathlib import Path

from pattern_engine import (
    analyze_exchange_pattern,
    get_optimal_window,
    predict_next_exchange,
    should_execute_now,
)

HISTORY_PATH = Path(__file__).parent / "data" / "history.json"

transactions = json.loads(HISTORY_PATH.read_text())

print("=" * 55)
print("analyze_exchange_pattern()")
print("=" * 55)
pattern = analyze_exchange_pattern(transactions)
for key, value in pattern.items():
    print(f"  {key:<30} {value}")

print()
print("=" * 55)
print("should_execute_now(current_rate=364.28)")
print("=" * 55)
decision = should_execute_now(364.28, transactions)
for key, value in decision.items():
    print(f"  {key:<30} {value}")

print()
print("=" * 55)
print("get_optimal_window()")
print("=" * 55)
window = get_optimal_window(transactions)
for key, value in window.items():
    print(f"  {key:<30} {value}")

print()
print("=" * 55)
print("predict_next_exchange()")
print("=" * 55)
next_date = predict_next_exchange(transactions)
print(f"  {'next_predicted_exchange':<30} {next_date}")
