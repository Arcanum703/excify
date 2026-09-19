from collections import Counter
from datetime import date, timedelta

import numpy as np
import pandas as pd


def analyze_exchange_pattern(transactions: list) -> dict:
    if not transactions:
        return {}

    df = _to_df(transactions)

    # Day of week pattern
    day_counts = df["day_of_week"].value_counts()
    usual_day = day_counts.index[0]
    day_confidence = int(round(day_counts.iloc[0] / len(df) * 100))

    # Time window pattern
    hours = df["hour"]
    h_mean = hours.mean()
    h_std = hours.std() if len(hours) > 1 else 1.0
    window_start = max(0, int(h_mean - h_std))
    window_end = min(23, int(h_mean + h_std) + 1)
    usual_window = f"{window_start:02d}:00-{window_end:02d}:00"
    in_window = ((hours >= window_start) & (hours <= window_end)).sum()
    time_confidence = int(round(in_window / len(df) * 100))

    avg_amount = round(float(df["amount_eur"].mean()), 2)

    # Average frequency between consecutive transactions
    if len(df) > 1:
        df_sorted = df.sort_values("date")
        deltas = df_sorted["date"].diff().dropna().dt.days
        frequency_days = int(round(deltas.mean()))
    else:
        frequency_days = 7

    rate_sensitivity = calculate_rate_sensitivity(transactions)
    overall_confidence = int(round((day_confidence + time_confidence) / 2))

    next_exchange = predict_next_exchange(transactions)

    pattern_summary = (
        f"Exchanges every {usual_day} {usual_window.split('-')[0][:2]}:00 window, "
        f"avg €{avg_amount:,.0f} per transaction"
    )

    return {
        "usual_day": usual_day,
        "day_confidence": day_confidence,
        "usual_window": usual_window,
        "time_confidence": time_confidence,
        "avg_amount": avg_amount,
        "frequency_days": frequency_days,
        "rate_sensitivity": rate_sensitivity,
        "overall_confidence": overall_confidence,
        "next_predicted_exchange": next_exchange,
        "pattern_summary": pattern_summary,
    }


def calculate_rate_sensitivity(transactions: list) -> float:
    if len(transactions) < 2:
        return 0.0

    df = _to_df(transactions).sort_values("date").reset_index(drop=True)
    rates = df["rate_used"].values
    scores = []

    for i, row in df.iterrows():
        # Use a rolling 7-transaction window around each point as a proxy for the
        # 7-day average (history doesn't store contemporaneous market data).
        window_start = max(0, i - 3)
        window_end = min(len(rates), i + 4)
        window_rates = np.delete(rates[window_start:window_end], i - window_start)
        if len(window_rates) == 0:
            continue
        avg = window_rates.mean()
        if avg == 0:
            continue
        # Score 1 if the user's rate is at or above the local average, else 0
        scores.append(1.0 if row["rate_used"] >= avg else 0.0)

    return round(float(np.mean(scores)), 4) if scores else 0.0


def predict_next_exchange(transactions: list) -> str:
    if not transactions:
        return str(date.today() + timedelta(days=7))

    df = _to_df(transactions).sort_values("date")
    last_date = df["date"].iloc[-1].date()

    if len(df) > 1:
        deltas = df["date"].diff().dropna().dt.days
        freq = int(round(deltas.mean()))
    else:
        freq = 7

    next_date = last_date + timedelta(days=freq)
    return str(next_date)


def get_optimal_window(transactions: list) -> dict:
    if not transactions:
        return {"best_day": "Friday", "best_hour": 9, "avg_rate_at_window": 0.0}

    df = _to_df(transactions)

    # Group by day-of-week and hour, take the mean rate for each bucket
    grouped = df.groupby(["day_of_week", "hour"])["rate_used"].mean()
    best_day, best_hour = grouped.idxmax()
    avg_rate = round(float(grouped.max()), 4)

    return {
        "best_day": best_day,
        "best_hour": int(best_hour),
        "avg_rate_at_window": avg_rate,
    }


def should_execute_now(current_rate: float, transactions: list) -> dict:
    # Hard limits and AI execution authority are enforced by autopilot, not sample analysis.
    avg_rate = _last_n_avg(transactions, 30)
    return _decision_payload("hold", "Sample-history analysis never authorizes an exchange. A validated AI timing decision is required.", current_rate, avg_rate)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_df(transactions: list) -> pd.DataFrame:
    df = pd.DataFrame(transactions)
    df["date"] = pd.to_datetime(df["date"])
    df["hour"] = pd.to_datetime(df["time"], format="%H:%M").dt.hour
    df["amount_eur"] = df["amount_eur"].astype(float)
    df["rate_used"] = df["rate_used"].astype(float)
    return df


def _last_n_avg(transactions: list, days: int) -> float:
    if not transactions:
        return 0.0
    df = _to_df(transactions).sort_values("date")
    cutoff = df["date"].max() - pd.Timedelta(days=days)
    recent = df[df["date"] >= cutoff]
    if recent.empty:
        recent = df
    return round(float(recent["rate_used"].mean()), 6)


def _decision_payload(
    decision: str,
    reason: str,
    current_rate: float,
    avg_rate: float,
    threshold: float | None = None,
) -> dict:
    if threshold is None:
        threshold = round(avg_rate * 1.005, 6)
    rate_diff_pct = round((current_rate - avg_rate) / avg_rate * 100, 2) if avg_rate else 0.0
    return {
        "decision": decision,
        "reason": reason,
        "current_rate": current_rate,
        "avg_rate": avg_rate,
        "rate_diff_pct": rate_diff_pct,
        "threshold_used": threshold,
    }
