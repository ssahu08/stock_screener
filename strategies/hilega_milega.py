# Hilega Milega Strategy Implementation
# ========================================
# Strategy Rules (as described by user):
# 
# 1. RSI length 9 (not default 14)
# 2. Overbought/oversold levels BOTH set to 50
#    - turns RSI into a bull/bear momentum line
# 3. 3-period EMA plotted on RSI itself (green line)
# 4. 21-period WMA plotted on RSI itself (red line)
#    - weighting acts as a stand-in for "volume"
#    - "the single most important line in the system"
# 
# READINGS:
# 
# - RSI above 50 = strength → only look for longs
#   Below 50 → only look for shorts
# 
# - The red line (21 WMA of RSI) is the key:
#   as long as it stays inside the "strength zone," the current
#   trend is intact. When it pushes back outside that zone,
#   that's the earliest sign a bottom (or top) is forming.
# 
# - Distance between all three lines = momentum intensity
#   - Wide gaps = fast, gap-up/gap-down moves
#   - Lines converging = momentum fading
# 
# - The green line touching the RSI line =
#   early warning to book profits and wait, not enter fresh
# 
# - ENTRY CONFIRMATION:
#   - Longs: wait for a candle to close above
#     the previous candle's high
#   - Shorts: wait for a candle to close below
#     the previous candle's low
# 
# - STOP-LOSS:
#   - Longs: the low of the candle where the red line first
#     broke out of the zone, on a closing basis
#   - Shorts: the high of the candle where the red line first
#     broke out of the zone, on a closing basis
# 
# - TIMEFRAMES:
#   - Daily/Weekly for trading
#   - Monthly for investing
#   - Intraday: hourly (min 15-min if faster)
#   - Start trades after 10:15 AM to skip opening noise
# 
# - BEGINNER ADD-ON:
#   - Combine with Bollinger Bands:
#     buy above the 20 SMA with RSI-line > 50
#     stop at candle low, target the upper band
#
# - CLAIMED ACCURACY:
#   ~70–75% (trader's own estimate, not independently verified)


import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from strategies.base import Strategy


class HilegaMilegaStrategy(Strategy):
    """Hilega Milega momentum indicator strategy on NSE equities.
    
    Calculates:
    - RSI(9) with 50/50 overbought/oversold levels
    - WMA(21) of RSI (the red line - most important)
    - EMA(3) of WMA (the green line)
    """

    @property
    def name(self) -> str:
        return "Hilega Milega"

    @property
    def key(self) -> str:
        return "hilega_milega"

    @property
    def min_bars(self) -> int:
        return 22  # enough for 21-period WMA + buffer

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate all three Hilega Milega indicators."""
        # --- RSI 9-period ---
        delta = df["Close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=9).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=9).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))

        # --- WMA 21 of RSI ---
        weights = np.arange(1, 22)  # linearly increasing weights 1 to 21
        df["WMA"] = df["RSI"].rolling(21).apply(
            lambda p: np.dot(p, weights) / weights.sum(), raw=True
        )

        # --- EMA 3 of WMA ---
        df["EMA"] = df["WMA"].ewm(span=3, adjust=False).mean()

        return df

    def buy_signal(self, df: pd.DataFrame) -> pd.Series:
        """Boolean Series: True where BUY conditions are met (every historical row).

        BUY Conditions:
        1. RSI > 50 (bullish territory)
        2. WMA > 50 (trend intact in strength zone)
        3. Close > previous week's high (candle confirmation)

        Vectorized over the full frame so the engine can backtest
        historical entries, not just evaluate the latest bar.
        """
        result = (
            (df["RSI"] > 50)
            & (df["WMA"] > 50)
            & (df["Close"] > df["High"].shift(1))
        )
        return result.fillna(False).astype(bool)

    def sell_signal(self, df: pd.DataFrame) -> pd.Series:
        """Boolean Series: True where SELL conditions are met (every historical row).

        SELL Conditions:
        1. RSI < 50 (bearish territory)
        2. WMA < 50 (trend intact in bearish zone)
        3. Close < previous week's low (candle confirmation)
        """
        result = (
            (df["RSI"] < 50)
            & (df["WMA"] < 50)
            & (df["Close"] < df["Low"].shift(1))
        )
        return result.fillna(False).astype(bool)

    def format_metrics(self, df: pd.DataFrame) -> str:
        """Format latest indicator values for display."""
        latest = df.iloc[-1]
        trend = "Bullish" if latest["RSI"] > 50 else "Bearish"
        return (
            f"RSI: {round(latest['RSI'], 1)} | "
            f"WMA: {round(latest['WMA'], 1)} | "
            f"EMA: {round(latest['EMA'], 1)} | "
            f"Trend: {trend}"
        )

    def sort_value(self, df: pd.DataFrame) -> float:
        """Rank BUY signals by RSI (higher = stronger bullish momentum)."""
        return float(df.iloc[-1]["RSI"])

    # ------------------------------------------------------------------
    # Additional utility methods for the engine
    # ------------------------------------------------------------------

    def detect_zone_breakout(self, df: pd.DataFrame) -> dict:
        """Detect whether the red line (WMA) has broken out of its zone NOW.

        Strategy rule: "Stop-loss = the low (longs) / high (shorts) of the
        candle where the red line FIRST broke out of the zone, on a closing
        basis."

        Interpretation for a scanner:
        - Determine the current trend from the latest RSI (>50 long, <50 short)
        - A breach is the current CONTIGUOUS run of bars where WMA sits on the
          wrong side of 50 (<=50 during a long, >=50 during a short)
        - stop_loss = low of the first bar of that run (longs) or
          high of the first bar of that run (shorts)

        Fresh BUY setups have WMA > 50, so they report broke_out=False with no
        stop — the stop only appears once the red line actually leaves the zone.

        Returns: {"broke_out", "breakout_candle_low", "breakout_candle_high",
                  "direction", "stop_loss"}
        """
        out = {
            "broke_out": False,
            "breakout_candle_low": None,
            "breakout_candle_high": None,
            "direction": None,
            "stop_loss": None,
        }
        if len(df) < 3 or df[["RSI", "WMA"]].iloc[-1].isna().any():
            return out

        last = len(df) - 1
        latest_rsi = df["RSI"].iloc[last]

        if latest_rsi > 50:
            direction = "long"

            def breached(i: int) -> bool:
                rsi, wma = df["RSI"].iloc[i], df["WMA"].iloc[i]
                return not pd.isna(wma) and wma <= 50

        elif latest_rsi < 50:
            direction = "short"

            def breached(i: int) -> bool:
                rsi, wma = df["RSI"].iloc[i], df["WMA"].iloc[i]
                return not pd.isna(wma) and wma >= 50

        else:
            return out

        if not breached(last):
            return out  # zone intact — no active stop needed

        # Walk back to the first bar of the current breach run
        j = last
        while j > 0 and breached(j - 1):
            j -= 1

        out["broke_out"] = True
        out["direction"] = direction
        out["breakout_candle_low"] = float(df["Low"].iloc[j])
        out["breakout_candle_high"] = float(df["High"].iloc[j])
        # Stop: low of breaking candle for longs, high for shorts
        out["stop_loss"] = (
            out["breakout_candle_low"] if direction == "long"
            else out["breakout_candle_high"]
        )
        return out

    def profit_taking_warning(self, df: pd.DataFrame) -> bool:
        """Check if green line (EMA) touched the RSI line.
        
        Returns True if EMA and RSI are very close (within ~1 point).
        This is an early warning to book profits, NOT a hard exit signal.
        """
        latest = df.iloc[-1]
        diff = abs(latest["RSI"] - latest["EMA"])
        return diff < 1.0  # within 1 point