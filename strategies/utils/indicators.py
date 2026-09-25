# Shared Technical Indicators
# ===========================
# Reusable indicators for all strategies

import pandas as pd
import numpy as np


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """
    Supertrend indicator.
    Returns df with columns: supertrend, trend (1=bullish, -1=bearish), flip (signal)
    """
    df = df.copy()
    
    # ATR
    prev_close = df["Close"].shift(1)
    tr = np.maximum(
        df["High"] - df["Low"],
        np.maximum((df["High"] - prev_close).abs(), (df["Low"] - prev_close).abs())
    )
    atr = tr.rolling(period).mean()
    
    # Basic bands
    hl2 = (df["High"] + df["Low"]) / 2
    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)
    
    # Final bands
    final_upper = upper_band.copy()
    final_lower = lower_band.copy()
    
    for i in range(1, len(df)):
        if df["Close"].iloc[i-1] > final_upper.iloc[i-1]:
            final_upper.iloc[i] = min(upper_band.iloc[i], final_upper.iloc[i-1])
        else:
            final_upper.iloc[i] = upper_band.iloc[i]
            
        if df["Close"].iloc[i-1] < final_lower.iloc[i-1]:
            final_lower.iloc[i] = max(lower_band.iloc[i], final_lower.iloc[i-1])
        else:
            final_lower.iloc[i] = lower_band.iloc[i]
    
    # Supertrend
    supertrend_vals = pd.Series(index=df.index, dtype=float)
    trend = pd.Series(index=df.index, dtype=int)
    
    supertrend_vals.iloc[0] = final_lower.iloc[0]
    trend.iloc[0] = 1
    
    for i in range(1, len(df)):
        if trend.iloc[i-1] == 1 and df["Close"].iloc[i] <= final_lower.iloc[i]:
            trend.iloc[i] = -1
            supertrend_vals.iloc[i] = final_upper.iloc[i]
        elif trend.iloc[i-1] == -1 and df["Close"].iloc[i] >= final_upper.iloc[i]:
            trend.iloc[i] = 1
            supertrend_vals.iloc[i] = final_lower.iloc[i]
        else:
            trend.iloc[i] = trend.iloc[i-1]
            supertrend_vals.iloc[i] = final_lower.iloc[i] if trend.iloc[i] == 1 else final_upper.iloc[i]
    
    df["supertrend"] = supertrend_vals
    df["trend"] = trend
    df["supertrend_flip"] = (trend != trend.shift(1)).astype(int)
    df["flip_to_bullish"] = (trend == 1) & (trend.shift(1) == -1)
    df["flip_to_bearish"] = (trend == -1) & (trend.shift(1) == 1)
    
    return df


def open_equals_low(df: pd.DataFrame, tolerance: float = 0.0) -> pd.Series:
    """Open == Low (exact or within tolerance %)"""
    if tolerance == 0:
        return (df["Open"] == df["Low"])
    return ((df["Open"] - df["Low"]).abs() / df["Low"] <= tolerance / 100)


def volume_filter(df: pd.DataFrame, min_avg_volume: int = 50000, period: int = 20) -> pd.Series:
    """Volume > min_avg_volume over period"""
    return df["Volume"].rolling(period).mean() > min_avg_volume


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range"""
    prev_close = df["Close"].shift(1)
    tr = np.maximum(
        df["High"] - df["Low"],
        np.maximum((df["High"] - df["Close"].shift(1)).abs(), (df["Low"] - df["Close"].shift(1)).abs())
    )
    return tr.rolling(period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index"""
    plus_dm = df["High"].diff()
    minus_dm = -df["Low"].diff()
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0)
    
    tr = np.maximum(
        df["High"] - df["Low"],
        np.maximum((df["High"] - df["Close"].shift(1)).abs(), (df["Low"] - df["Close"].shift(1)).abs())
    )
    tr_series = pd.Series(tr, index=df.index)
    
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(period).mean() / tr_series.rolling(period).mean())
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(period).mean() / tr_series.rolling(period).mean())
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.rolling(period).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Volume Weighted Average Price (session-based)"""
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    return (typical_price * df["Volume"]).cumsum() / df["Volume"].cumsum()