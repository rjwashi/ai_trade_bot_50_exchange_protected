import pandas as pd
import numpy as np

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Enforce numeric types (critical)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

    close = df["close"]
    volume = df["volume"]

    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = (100 - (100 / (1 + rs))).fillna(50)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()

    df["vol_avg20"] = volume.rolling(20).mean()

    return df


def score_setup(df: pd.DataFrame) -> dict:
    # Normalize numeric fields BEFORE indicators
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

    if len(df) < 60:
        return {"score": 0, "reason": "not enough candles"}

    ind = add_indicators(df)
    x = ind.iloc[-1]
    prev = ind.iloc[-2]

    score = 0
    reasons = []

    if x["ema20"] > x["ema50"]:
        score += 20
        reasons.append("EMA20 > EMA50")

    if 52 <= x["rsi"] <= 68:
        score += 15
        reasons.append(f"RSI {x['rsi']:.1f}")

    if x["macd"] > x["macd_signal"] and prev["macd"] <= prev["macd_signal"]:
        score += 15
        reasons.append("fresh MACD bullish cross")
    elif x["macd"] > x["macd_signal"]:
        score += 8
        reasons.append("MACD bullish")

    # Now guaranteed safe: both are floats
    if pd.notna(x["vol_avg20"]) and x["volume"] > x["vol_avg20"]:
        score += 15
        reasons.append("volume above 20-bar average")

    if x["close"] > prev["close"]:
        score += 10
        reasons.append("positive latest candle")

    if x["close"] > x["ema20"]:
        score += 10
        reasons.append("price above EMA20")

    score += 15
    reasons.append("configured reward:risk = 2:1")

    return {
        "score": int(score),
        "price": float(x["close"]),
        "rsi": float(x["rsi"]),
        "reasons": reasons,
    }
