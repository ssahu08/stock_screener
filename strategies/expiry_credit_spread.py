# Expiry Day Credit Spread Strategy v2
# =====================================
# Improved version with:
# - 15-min chart (resampled from 5-min) for less noise
# - Strong trend filter: price > VWAP for 2+ consecutive candles
# - ADX > 20 for trend strength confirmation
# - Volume confirmation on entry
# - Fixed intraday stop loss (1.5x ATR) + forced same-day exit at 14:30 IST
# - Proper risk:reward (1:3 target)
# - Only expiry day (NSE: TUESDAYS since 01-Sep-2025; Thursdays before),
#   9:30-11:30 entry window

import pandas as pd
import numpy as np
from datetime import time, datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from strategies.base import Strategy
from config.keys import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

@dataclass
class SpreadConfig:
    """Configuration for credit spreads."""
    index: str = "NIFTY"           # "NIFTY" or "BANKNIFTY"
    lot_size: int = 65             # NIFTY=65, BANKNIFTY=30 (SEBI revision, eff. Jan-2026)
    strike_step: int = 50          # NIFTY=50, BANKNIFTY=100
    spread_width_pct: float = 1.0  # Width between strikes (% of spot)
    short_strike_pct: float = 1.0  # Short strike OTM (%)
    target_credit_pct: float = 0.35 # Target credit as % of width (35%)
    max_loss_pct: float = 0.02     # Max loss per spread as % of capital (2%)
    max_spreads: int = 2           # Max concurrent spreads
    # Times in UTC (Fyers returns UTC timestamps)
    # 9:30 IST = 04:00 UTC, 11:30 IST = 06:00 UTC, 14:30 IST = 09:00 UTC
    entry_window_start: time = time(4, 0)
    entry_window_end: time = time(6, 0)
    exit_time: time = time(9, 0)   # Exit by 14:30 IST = 09:00 UTC
    vwap_period: int = 20
    ema_period: int = 20
    adx_period: int = 14
    adx_threshold: float = 20.0    # Minimum ADX for trend strength
    atr_period: int = 14
    atr_mult: float = 1.5          # ATR multiplier for fixed stop loss
    trend_confirm_candles: int = 2 # Consecutive candles above/below VWAP
    volume_mult: float = 1.0       # Volume > 1.0x 20-period avg
    chart_interval: str = "15min"  # Base chart interval


DEFAULT_CONFIG = SpreadConfig()


# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Expiry-day regimes (NSE)
# -----------------------------------------------------------------------------
# * Until 31-Aug-2025 NSE index derivatives expired on THURSDAYS.
# * W.e.f. 01-Sep-2025 (NSE circular NSE/FAOP/68685) ALL NSE index & stock
#   derivatives expire on TUESDAYS.  If Tuesday is a trading holiday the
#   expiry shifts to the PREVIOUS trading day.
# * BANKNIFTY/FINNIFTY/MIDCPNIFTY weekly expiry was discontinued from
#   20-Nov-2024 (SEBI: one weekly index per exchange — NSE kept NIFTY 50).
#   BANKNIFTY now expires monthly only: last Thursday until Aug-2025,
#   last Tuesday from Sep-2025.
# * One-time transition: the 04-Sep-2025 weekly expiry moved to 09-Sep-2025,
#   so there was NO expiry in the 01–08 Sep-2025 window.

TUESDAY_RULE_FROM = pd.Timestamp("2025-09-01")
BANKNIFTY_MONTHLY_ONLY_FROM = pd.Timestamp("2024-11-20")
_NIFTY_TRANSITION_GAP = (pd.Timestamp("2025-09-01"), pd.Timestamp("2025-09-09"))
_WEEKDAY_FREQ = {0: "W-MON", 1: "W-TUE", 2: "W-WED", 3: "W-THU", 4: "W-FRI"}


def is_expiry_day(dt: pd.Timestamp, index_name: str = "NIFTY") -> bool:
    """True if `dt` falls on a scheduled NSE expiry weekday (regime-aware).

    Weekday-only approximation — it does not know the exchange holiday
    calendar.  For exact backtest masks (holiday shifts included) use
    compute_expiry_mask().
    """
    dt = pd.Timestamp(dt)
    if index_name == "NIFTY":
        if dt < TUESDAY_RULE_FROM:
            return dt.dayofweek == 3  # Thursday era
        if _NIFTY_TRANSITION_GAP[0] <= dt < _NIFTY_TRANSITION_GAP[1]:
            return False  # one-time Sep-2025 transition week
        return dt.dayofweek == 1  # Tuesday era (weekly = every Tuesday)
    # BANKNIFTY: weekly Thursday until 20-Nov-2024, monthly only afterwards
    if dt < BANKNIFTY_MONTHLY_ONLY_FROM:
        return dt.dayofweek == 3
    last_weekday = 3 if dt < TUESDAY_RULE_FROM else 1  # last Thu -> last Tue
    return dt.dayofweek == last_weekday and (dt + pd.Timedelta(days=7)).month != dt.month


def _scheduled_days(first: pd.Timestamp, last: pd.Timestamp, weekday: int) -> pd.DatetimeIndex:
    """Calendar dates with `weekday` (0=Mon) in [first, last]."""
    return pd.date_range(first, last, freq=_WEEKDAY_FREQ[weekday])


def _last_of_month(days: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Final occurrence per calendar month (all `days` share one weekday)."""
    if len(days) == 0:
        return days
    s = pd.Series(days)
    return pd.DatetimeIndex(s.groupby(s.dt.to_period("M")).max())


def compute_expiry_dates(trading_days, index_name: str = "NIFTY") -> set:
    """Exact expiry dates implied by the trading days present in the data.

    Applies the regime rules above, then adjusts each scheduled expiry to the
    previous trading day when the scheduled day is an exchange holiday (the
    data only contains bars on trading days).
    """
    days = pd.DatetimeIndex(sorted({pd.Timestamp(d).normalize() for d in trading_days}))
    days = days[days.dayofweek < 5]
    if len(days) == 0:
        return set()

    def _adjust(schedule: pd.DatetimeIndex) -> set:
        out = set()
        for sched in schedule:
            pos = int(days.searchsorted(sched, side="right")) - 1
            if pos >= 0:
                cand = days[pos]
                if (sched - cand).days < 7:  # allow only small holiday shifts
                    out.add(cand)
        return out

    expiries = set()

    if index_name == "NIFTY":
        thu = _scheduled_days(days[0], days[-1], 3)
        thu = thu[thu < TUESDAY_RULE_FROM]
        tue = _scheduled_days(days[0], days[-1], 1)
        tue = tue[
            (tue >= TUESDAY_RULE_FROM)
            & ~((tue >= _NIFTY_TRANSITION_GAP[0]) & (tue < _NIFTY_TRANSITION_GAP[1]))
        ]
        expiries |= _adjust(thu) | _adjust(tue)
    else:
        # Weekly-Thursday era (all Thursdays before the weekly ban)
        thu = _scheduled_days(days[0], days[-1], 3)
        expiries |= _adjust(thu[thu < BANKNIFTY_MONTHLY_ONLY_FROM])
        # Monthly only afterwards: last Thursday, then last Tuesday from Sep-2025
        monthly = _last_of_month(thu)
        monthly = monthly[
            (monthly >= BANKNIFTY_MONTHLY_ONLY_FROM) & (monthly < TUESDAY_RULE_FROM)
        ]
        expiries |= _adjust(monthly)
        tue = _last_of_month(_scheduled_days(days[0], days[-1], 1))
        expiries |= _adjust(tue[tue >= TUESDAY_RULE_FROM])

    return expiries


def compute_expiry_mask(index: pd.DatetimeIndex, index_name: str = "NIFTY") -> pd.Series:
    """Boolean Series over `index`: True on expiry-day bars."""
    expiries = compute_expiry_dates(index, index_name)
    if not expiries:
        return pd.Series(False, index=index)
    return pd.Series(pd.DatetimeIndex(index).normalize().isin(expiries), index=index)


def resample_to_15min(df_5min: pd.DataFrame) -> pd.DataFrame:
    """Resample 5-min data to 15-min OHLCV."""
    if df_5min.empty:
        return pd.DataFrame()
    
    df = df_5min.resample('15min').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
    
    return df


def get_expiry_strikes(spot: float, config: SpreadConfig) -> Dict[str, float]:
    """Calculate strike prices for credit spread."""
    strike_step = config.strike_step

    # Round spot to nearest strike
    atm = round(spot / strike_step) * strike_step

    # Short strike: 1% OTM
    short_dist = spot * config.short_strike_pct / 100
    short_strike = atm - round(short_dist / strike_step) * strike_step

    # Long strike: spread_width further OTM
    width_dist = spot * config.spread_width_pct / 100
    long_strike = short_strike - round(width_dist / strike_step) * strike_step

    return {
        "atm": atm,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "width": short_strike - long_strike,
    }


def calculate_margin(config: SpreadConfig, width: float) -> float:
    """Estimate margin required per spread (approx)."""
    notional = width * config.lot_size
    return notional * 0.18  # ~18% of width * lot


# -----------------------------------------------------------------------------
# Technical Indicators
# -----------------------------------------------------------------------------

def compute_indicators(df: pd.DataFrame, config: SpreadConfig) -> pd.DataFrame:
    """Add all technical indicators to 15-min dataframe."""
    df = df.copy()

    # VWAP (session-based)
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    df["VWAP"] = (typical_price * df["Volume"]).cumsum() / df["Volume"].cumsum()

    # EMA(20)
    df["EMA20"] = df["Close"].ewm(span=config.ema_period, adjust=False).mean()

    # ATR
    prev_close = df["Close"].shift(1)
    tr = np.maximum(
        df["High"] - df["Low"],
        np.maximum((df["High"] - prev_close).abs(), (df["Low"] - prev_close).abs())
    )
    df["ATR"] = tr.rolling(config.atr_period).mean()

    # ADX
    plus_dm = df["High"].diff()
    minus_dm = -df["Low"].diff()
    plus_dm = np.where((plus_dm > minus_dm) & (plus_dm > 0), plus_dm, 0)
    minus_dm = np.where((minus_dm > plus_dm) & (minus_dm > 0), minus_dm, 0)
    
    tr_series = pd.Series(tr, index=df.index)
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(config.adx_period).mean() / tr_series.rolling(config.adx_period).mean())
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(config.adx_period).mean() / tr_series.rolling(config.adx_period).mean())
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    df["ADX"] = dx.rolling(config.adx_period).mean()

    # Trend: Price above both VWAP and EMA
    df["TREND_UP"] = (df["Close"] > df["VWAP"]) & (df["Close"] > df["EMA20"])
    df["TREND_DOWN"] = (df["Close"] < df["VWAP"]) & (df["Close"] < df["EMA20"])

    # Strong trend: price > VWAP for N consecutive candles
    df["TREND_UP_STRONG"] = df["TREND_UP"].rolling(config.trend_confirm_candles).apply(lambda x: x.all(), raw=True).fillna(0).astype(bool)
    df["TREND_DOWN_STRONG"] = df["TREND_DOWN"].rolling(config.trend_confirm_candles).apply(lambda x: x.all(), raw=True).fillna(0).astype(bool)

    # Volume
    df["VOL_MA20"] = df["Volume"].rolling(20).mean()
    df["VOLUME_SPIKE"] = df["Volume"] > (df["VOL_MA20"] * config.volume_mult)

    # Pullback: price near VWAP/EMA zone
    vwap_dist = (df["Close"] - df["VWAP"]).abs() / df["Close"]
    ema_dist = (df["Close"] - df["EMA20"]).abs() / df["Close"]
    df["NEAR_VWAP"] = vwap_dist < 0.002  # 0.2%
    df["NEAR_EMA"] = ema_dist < 0.002

    # Reclaim: was near VWAP/EMA, now trending strongly
    df["RECLAIM_UP"] = (
        (df["NEAR_VWAP"].shift(1) | df["NEAR_EMA"].shift(1))
        & df["TREND_UP_STRONG"]
    )
    df["RECLAIM_DOWN"] = (
        (df["NEAR_VWAP"].shift(1) | df["NEAR_EMA"].shift(1))
        & df["TREND_DOWN_STRONG"]
    )

    # Time filters (UTC times)
    df["TIME"] = df.index.time
    df["IN_ENTRY_WINDOW"] = (
        (df["TIME"] >= time(4, 0)) &
        (df["TIME"] <= time(6, 0))
    )
    df["IS_EXPIRY"] = compute_expiry_mask(df.index, config.index)

    return df


def generate_signals(df: pd.DataFrame, config: SpreadConfig) -> pd.DataFrame:
    """Generate entry signals for credit spreads."""
    df = df.copy()

    # Bull Put: Expiry day, in window, strong trend up, reclaim, ADX > threshold, volume spike
    df["SIGNAL_BULL_PUT"] = (
        df["IS_EXPIRY"] &
        df["IN_ENTRY_WINDOW"] &
        df["TREND_UP_STRONG"] &
        df["RECLAIM_UP"] &
        (df["ADX"] > config.adx_threshold) &
        df["VOLUME_SPIKE"]
    )

    # Bear Call: Expiry day, in window, strong trend down, reclaim, ADX > threshold, volume spike
    df["SIGNAL_BEAR_CALL"] = (
        df["IS_EXPIRY"] &
        df["IN_ENTRY_WINDOW"] &
        df["TREND_DOWN_STRONG"] &
        df["RECLAIM_DOWN"] &
        (df["ADX"] > config.adx_threshold) &
        df["VOLUME_SPIKE"]
    )

    # First signal of the day only
    for sig_col in ["SIGNAL_BULL_PUT", "SIGNAL_BEAR_CALL"]:
        daily_first = df.groupby(df.index.date)[sig_col].transform("idxmax")
        df[f"{sig_col}_FIRST"] = df.index.isin(daily_first) & df[sig_col]

    return df


# -----------------------------------------------------------------------------
# Trade Simulation
# -----------------------------------------------------------------------------

def calculate_margin(config: SpreadConfig, width: float) -> float:
    notional = width * config.lot_size
    return notional * 0.18


def get_expiry_strikes(spot: float, config: SpreadConfig) -> Dict[str, float]:
    strike_step = config.strike_step
    atm = round(spot / strike_step) * strike_step
    short_dist = spot * config.short_strike_pct / 100
    short_strike = atm - round(short_dist / strike_step) * strike_step
    width_dist = spot * config.spread_width_pct / 100
    long_strike = short_strike - round(width_dist / strike_step) * strike_step
    return {
        "atm": atm,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "width": short_strike - long_strike,
    }


def calculate_margin(config: SpreadConfig, width: float) -> float:
    notional = width * config.lot_size
    return notional * 0.18


def get_expiry_strikes(spot: float, config: SpreadConfig) -> Dict[str, float]:
    strike_step = config.strike_step
    atm = round(spot / strike_step) * strike_step
    short_dist = spot * config.short_strike_pct / 100
    short_strike = atm - round(short_dist / strike_step) * strike_step
    width_dist = spot * config.spread_width_pct / 100
    long_strike = short_strike - round(width_dist / strike_step) * strike_step
    return {
        "atm": atm,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "width": short_strike - long_strike,
    }


def simulate_trade(
    df: pd.DataFrame,
    entry_loc: int,
    direction: str,
    config: SpreadConfig,
    capital: float
) -> Optional[Dict]:
    """Simulate trade with fixed intraday stop + forced same-day exit at 14:30 IST (09:00 UTC)."""
    entry_time = df.index[entry_loc]
    spot = df["Close"].iloc[entry_loc]
    entry_atr = df["ATR"].iloc[entry_loc]

    if pd.isna(entry_atr) or entry_atr <= 0:
        return None

    # Calculate strikes
    strikes = get_expiry_strikes(spot, config)
    short_strike = strikes["short_strike"]
    long_strike = strikes["long_strike"]
    width = strikes["width"]

    # Estimate credit
    credit = width * config.target_credit_pct

    # Position sizing
    margin = width * config.lot_size * 0.18
    max_spreads = int(capital * config.max_loss_pct / (width * config.lot_size * 0.18))
    lots = min(config.max_spreads, max(1, max_spreads))

    # Fixed intraday stop (1.5x ATR) + forced same-day exit at 14:30 IST (09:00 UTC)
    stop_distance = df["ATR"].iloc[entry_loc] * 1.5
    entry_price = df["Close"].iloc[entry_loc]
    stop_price = entry_price - entry_atr * 1.5 if direction == "BULL_PUT" else entry_price + entry_atr * 1.5

    exit_loc = None
    exit_reason = "TIME"
    exit_price = None

    for i in range(entry_loc + 1, len(df)):
        row = df.iloc[i]
        t = row.name.time()

        # FORCED EXIT at 14:30 IST (09:00 UTC) - NO OVERNIGHT HOLDING
        if t >= time(9, 0):
            exit_loc = i
            exit_reason = "TIME_EXIT"
            exit_price = row["Close"]
            break

        # Intraday fixed stop loss (no trailing)
        if direction == "BULL_PUT":
            if row["Low"] <= stop_price:
                exit_loc = i
                exit_reason = "SL_FIXED"
                exit_price = stop_price
                break
        else:
            if row["High"] >= stop_price:
                exit_loc = i
                exit_reason = "SL_FIXED"
                exit_price = stop_price
                break

    if exit_loc is None or exit_loc <= entry_loc:
        return None

    # P&L estimation
    if exit_reason == "SL_FIXED":
        # Stop hit: assume lose 50% of max loss (stop at 1.5 ATR)
        pnl = -width * config.lot_size * lots * 0.5
    else:
        # Time exit: keep 60% of credit (theta decay + some directional)
        pnl = width * config.target_credit_pct * config.lot_size * lots * 0.6

    return {
        "entry_time": df.index[entry_loc],
        "exit_time": df.index[exit_loc],
        "direction": direction,
        "spot": spot,
        "short_strike": short_strike,
        "long_strike": long_strike,
        "width": width,
        "credit": width * config.target_credit_pct,
        "lots": lots,
        "pnl": pnl,
        "exit_reason": exit_reason,
        "max_profit": width * config.target_credit_pct * config.lot_size * lots,
        "max_loss": width * config.lot_size * lots - (width * config.target_credit_pct * config.lot_size * lots),
    }


# -----------------------------------------------------------------------------
# Strategy Class
# -----------------------------------------------------------------------------

class ExpiryCreditSpread(Strategy):
    """
    Expiry Day Credit Spread Strategy v2.
    
    Rules:
    - Only expiry day: NIFTY = every Tuesday (holiday -> previous trading
      day); BANKNIFTY = monthly, last Tuesday (weekly expiry discontinued
      20-Nov-2024).  Before 01-Sep-2025 expiry fell on Thursdays.
    - 15-min chart (resampled from 5-min)
    - Strong trend: Price > VWAP & EMA for 2+ consecutive candles, ADX > 20
    - Entry: Pullback to VWAP/EMA zone, then reclaim with volume spike
    - Exit: 14:30 IST (forced) or fixed stop loss (1.5x ATR)
    - Target: Keep 60% of credit (theta decay)
    - Risk: 2% capital per spread, max 2 spreads
    - NO OVERNIGHT HOLDING - exit by 14:30 IST
    """

    def __init__(self, config: SpreadConfig = None):
        self._config = config or DEFAULT_CONFIG

    @property
    def name(self) -> str:
        return f"Expiry Credit Spread ({self._config.index})"

    @property
    def key(self) -> str:
        return f"expiry_credit_spread_{self._config.index.lower()}"

    @property
    def timeframe(self) -> str:
        return self._config.chart_interval

    @property
    def min_bars(self) -> int:
        return 100

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        # Resample to 15-min first
        df_15 = resample_to_15min(df)
        df_15 = compute_indicators(df_15, self._config)
        df_15 = generate_signals(df_15, self._config)
        return df_15

    def buy_signal(self, df: pd.DataFrame) -> pd.Series:
        return df.get("SIGNAL_BULL_PUT_FIRST", pd.Series(False, index=df.index))

    def sell_signal(self, df: pd.DataFrame) -> pd.Series:
        return df.get("SIGNAL_BEAR_CALL_FIRST", pd.Series(False, index=df.index))

    def format_metrics(self, df: pd.DataFrame) -> str:
        last = df.iloc[-1]
        trend = "UP" if last.get("TREND_UP_STRONG", False) else ("DOWN" if last.get("TREND_DOWN_STRONG", False) else "FLAT")
        adx = last.get("ADX", 0)
        return f"Trend: {trend} | ADX: {adx:.1f} | VWAP: {last.get('VWAP', 0):.0f}"

    def sort_value(self, df: pd.DataFrame) -> float:
        return 0.0

    # -------------------------------------------------------------------------
    # Backtest
    # -------------------------------------------------------------------------

    def backtest(self, df: pd.DataFrame, capital: float = 50000) -> Dict[str, Any]:
        df = self.compute(df)

        expiry_days = df[df["IS_EXPIRY"] == True]
        if expiry_days.empty:
            return {"error": "No expiry days in data"}

        trades = []
        signals_bull = df[df["SIGNAL_BULL_PUT_FIRST"] == True]
        signals_bear = df[df["SIGNAL_BEAR_CALL_FIRST"] == True]

        for idx in signals_bull.index:
            loc = df.index.get_loc(idx)
            result = self._simulate_single(df, loc, "BULL_PUT", capital)
            if result:
                trades.append(result)

        for idx in signals_bear.index:
            loc = df.index.get_loc(idx)
            result = self._simulate_single(df, loc, "BEAR_CALL", capital)
            if result:
                trades.append(result)

        return self._analyze_trades(trades, capital)

    def _simulate_single(self, df: pd.DataFrame, entry_loc: int, direction: str, capital: float) -> Optional[Dict]:
        config = self._config
        entry_time = df.index[entry_loc]
        spot = df["Close"].iloc[entry_loc]
        entry_atr = df["ATR"].iloc[entry_loc]

        if pd.isna(entry_atr) or entry_atr <= 0:
            return None

        strikes = get_expiry_strikes(spot, config)
        short_strike = strikes["short_strike"]
        long_strike = strikes["long_strike"]
        width = strikes["width"]

        credit = width * config.target_credit_pct
        margin = width * config.lot_size * 0.18
        max_spreads = int(capital * config.max_loss_pct / (width * config.lot_size * 0.18))
        lots = min(config.max_spreads, max(1, max_spreads))

        # Fixed intraday stop (1.5x ATR) + forced same-day exit at 14:30 IST (09:00 UTC)
        stop_price = df["Close"].iloc[entry_loc] - df["ATR"].iloc[entry_loc] * 1.5 if direction == "BULL_PUT" else df["Close"].iloc[entry_loc] + df["ATR"].iloc[entry_loc] * 1.5

        exit_loc = None
        exit_reason = "TIME"
        exit_price = None

        for i in range(entry_loc + 1, len(df)):
            row = df.iloc[i]
            t = row.name.time()

            # FORCED EXIT at 14:30 IST (09:00 UTC) - NO OVERNIGHT HOLDING
            if t >= time(9, 0):
                exit_loc = i
                exit_reason = "TIME_EXIT"
                exit_price = row["Close"]
                break

            # Intraday fixed stop loss (no trailing)
            if direction == "BULL_PUT":
                if row["Low"] <= stop_price:
                    exit_loc = i
                    exit_reason = "SL_FIXED"
                    exit_price = stop_price
                    break
            else:
                if row["High"] >= stop_price:
                    exit_loc = i
                    exit_reason = "SL_FIXED"
                    exit_price = stop_price
                    break

        if exit_loc is None or exit_loc <= entry_loc:
            return None

        # P&L estimation
        if exit_reason == "SL_FIXED":
            pnl = -width * config.lot_size * lots * 0.5
        else:
            pnl = width * config.target_credit_pct * config.lot_size * lots * 0.6

        return {
            "entry_time": df.index[entry_loc],
            "exit_time": df.index[exit_loc],
            "direction": direction,
            "spot": df["Close"].iloc[entry_loc],
            "short_strike": strikes["short_strike"],
            "long_strike": strikes["long_strike"],
            "width": width,
            "credit": width * config.target_credit_pct,
            "lots": lots,
            "pnl": pnl,
            "exit_reason": exit_reason,
            "max_profit": width * config.target_credit_pct * config.lot_size * lots,
            "max_loss": width * config.lot_size * lots - (width * config.target_credit_pct * config.lot_size * lots),
        }

    def _analyze_trades(self, trades: List[Dict], capital: float) -> Dict:
        if not trades:
            return {"error": "No trades generated", "trades": 0}

        df = pd.DataFrame(trades)
        total_pnl = df["pnl"].sum()
        wins = df[df["pnl"] > 0]
        losses = df[df["pnl"] <= 0]

        return {
            "total_trades": len(df),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(df) * 100,
            "total_pnl": total_pnl,
            "avg_win": wins["pnl"].mean() if len(wins) > 0 else 0,
            "avg_loss": losses["pnl"].mean() if len(losses) > 0 else 0,
            "expectancy": df["pnl"].mean(),
            "profit_factor": abs(wins["pnl"].sum() / losses["pnl"].sum()) if losses["pnl"].sum() != 0 else float('inf'),
            "max_drawdown": (df["pnl"].cumsum().cummax() - df["pnl"].cumsum()).max(),
            "capital": capital,
            "return_pct": total_pnl / capital * 100,
            "trades_df": df,
        }


# -----------------------------------------------------------------------------
# Registry Registration
# -----------------------------------------------------------------------------

def register(registry_mod):
    @registry_mod.register_strategy
    class _BuiltInExpiryCreditSpreadNifty(ExpiryCreditSpread):
        def __init__(self):
            super().__init__(SpreadConfig(index="NIFTY"))

    @registry_mod.register_strategy
    class _BuiltInExpiryCreditSpreadBankNifty(ExpiryCreditSpread):
        def __init__(self):
            super().__init__(SpreadConfig(index="BANKNIFTY", lot_size=30, strike_step=100))