from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    return result.fillna(50.0)


def macd_hist(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    line = ema(series, fast) - ema(series, slow)
    sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return (line - sig).fillna(0.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().bfill()


def relative_volume(volume: pd.Series, window: int = 20) -> pd.Series:
    avg = volume.rolling(window, min_periods=max(5, window // 2)).mean().shift(1)
    return (volume / avg.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(1.0)


def session_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    denom = df["volume"].cumsum().replace(0, np.nan)
    return (pv.cumsum() / denom).ffill().bfill()


def crossover(a: pd.Series, b: pd.Series) -> int:
    if len(a) < 2 or len(b) < 2:
        return 0
    if a.iloc[-2] <= b.iloc[-2] and a.iloc[-1] > b.iloc[-1]:
        return 1
    if a.iloc[-2] >= b.iloc[-2] and a.iloc[-1] < b.iloc[-1]:
        return -1
    return 0


def trend_from_frame(df: pd.DataFrame, fast: int = 9, medium: int = 21) -> str:
    if len(df) < medium + 2:
        return "neutral"
    c = df["close"]
    ef, em = ema(c, fast), ema(c, medium)
    if c.iloc[-1] > ef.iloc[-1] > em.iloc[-1] and ef.iloc[-1] > ef.iloc[-3]:
        return "bullish"
    if c.iloc[-1] < ef.iloc[-1] < em.iloc[-1] and ef.iloc[-1] < ef.iloc[-3]:
        return "bearish"
    return "neutral"
