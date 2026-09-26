# Bollinger-Pivot Confluence Washout — TEST BENCH (not in production)
# ====================================================================
# Idea source: owner's Bigul/Kite screenshots (DRREDDY 1D, 26-Sep-2026).
#
#   Idea 1 (location): the LOWER Bollinger Band and ANY Standard Pivot
#           line sit close to each other — one blended support shelf.
#           Confirmed threshold (26-Sep-2026): gap <= 0.5% of price.
#
#   Idea 2 (trigger):  a candle that OPENS near the lower band and
#           CLOSES BELOW it — a washout that pierces the band straight
#           into the pivot support. The pivot holds and "price takes an
#           upwards move from the pivot as support" -> BUY the bounce.
#           Confirmed reading: close BELOW the band (washout), NOT a
#           reclaim close back above it.
#
# Timeframes:
#   1D  variant  = setup + trigger candle on daily bars (the idea as drawn)
#   60m variant  = the same rules on hourly bars for the 1-hour view;
#                  pivot lines default to the PREVIOUS SESSION's daily
#                  pivots (the levels watched intraday). Switch to
#                  standard pivots of the previous 60-min bar with
#                  BBPivotConfig(pivot_source="prev_bar").
#
# Standard pivot formulas (previous bar / previous session H,L,C):
#   P=(H+L+C)/3  R1=2P-L  S1=2P-H  R2=P+(H-L)  S2=P-(H-L)
#   R3=H+2(P-L)  S3=L-2(H-P)
#
# Execution plan carried in key_metrics:
#   signal : washout bar closes (confirm on bar close)
#   entry  : next session, 1H reclaim — hourly close back above BB_LO
#   stop   : low of the washout candle (below the pivot shelf)
#   target : BB middle band first (mean reversion), then next pivot up
#
# Long-only by design: sell_signal() never fires — a mirror top-side
# washout is a future extension, deliberately NOT invented here.
#
# Lives ONLY on feature/bb-pivot-test until the owner validates it on
# real charts; main/production never registers these keys.

from dataclasses import dataclass

import numpy as np
import pandas as pd

from strategies.base import Strategy

# Column order used when stacking the 7 pivot lines for nearest-match
PIVOT_NAMES = ("P", "R1", "R2", "R3", "S1", "S2", "S3")


@dataclass(frozen=True)
class BBPivotConfig:
    """Tuning — confirmed with the owner (26-Sep-2026)."""

    bb_len: int = 20            # BB period (owner's chart: 20, Close, simple)
    bb_std: float = 2.0         # BB deviation (owner's chart: 2.0)
    prox_pct: float = 0.5       # Idea 1: |BB_lo - nearest pivot| <= 0.5%
    open_near_pct: float = 0.5  # Idea 2: |Open - BB_lo| <= 0.5%
    pivot_source: str = "prev_bar"  # "prev_bar" | "prev_day"


def _f(val, nd: int = 2) -> float:
    """Round for display, mapping NaN/None to 0.0."""
    try:
        if val is None or pd.isna(val):
            return 0.0
        return round(float(val), nd)
    except (TypeError, ValueError):
        return 0.0


def _prev_session_hlc(df: pd.DataFrame) -> tuple:
    """(H, L, C) of the PREVIOUS SESSION mapped onto every intraday bar.

    All bars of session D get session D-1's aggregated high/low/close —
    i.e. the daily pivot lines you watch on an intraday chart. The first
    session gets NaN (nothing to reference yet).
    """
    keys = df.index.normalize()
    grp = df.groupby(keys, sort=True)
    prev_h = grp["High"].max().shift(1)
    prev_l = grp["Low"].min().shift(1)
    prev_c = grp["Close"].last().shift(1)
    return (
        pd.Series(prev_h.reindex(keys).to_numpy(), index=df.index),
        pd.Series(prev_l.reindex(keys).to_numpy(), index=df.index),
        pd.Series(prev_c.reindex(keys).to_numpy(), index=df.index),
    )


def _pivot_lines(df: pd.DataFrame, source: str) -> list:
    """Seven standard pivot lines as Series, ordered as PIVOT_NAMES.

    source="prev_bar": pivots of the previous bar of the frame's own
    timeframe (daily chart => yesterday, exactly as on the owner's
    screenshot). source="prev_day": pivots of the previous session
    (intraday frames see the same daily levels all day).
    """
    if source == "prev_day":
        h, l, c = _prev_session_hlc(df)
    else:
        h = df["High"].shift(1)
        l = df["Low"].shift(1)
        c = df["Close"].shift(1)

    p = (h + l + c) / 3.0
    rng = h - l
    return [
        p,            # P
        2 * p - l,    # R1
        p + rng,      # R2
        h + 2 * (p - l),  # R3
        2 * p - h,    # S1
        p - rng,      # S2
        l - 2 * (h - p),  # S3
    ]


class BBPivotWashout(Strategy):
    """Shared wiring: BB(20, 2.0) on closes + nearest-pivot confluence."""

    def __init__(self, cfg: BBPivotConfig = None):
        self.cfg = cfg or BBPivotConfig()

    @property
    def timeframe(self) -> str:
        return "daily"

    @property
    def min_bars(self) -> int:
        return 25  # BB(20) + pivot warm-up + margin

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        cfg = self.cfg
        df = df.copy()

        # Bollinger (owner's settings: 20 periods, Close, simple MA, 2σ)
        mid = df["Close"].rolling(cfg.bb_len).mean()
        std = df["Close"].rolling(cfg.bb_len).std(ddof=0)
        df["BB_MID"] = mid
        df["BB_UP"] = mid + cfg.bb_std * std
        df["BB_LO"] = mid - cfg.bb_std * std

        # Idea 1 — nearest standard pivot line to the lower band
        lines = _pivot_lines(df, cfg.pivot_source)  # ordered as PIVOT_NAMES
        pv = np.column_stack([s.to_numpy(dtype=float) for s in lines])
        bb = df["BB_LO"].to_numpy(dtype=float)

        gap = np.abs(pv - bb[:, None])
        with np.errstate(divide="ignore", invalid="ignore"):
            gap_pct = gap / np.abs(bb[:, None]) * 100.0  # % of the band level
        gap_pct = np.where(np.isfinite(bb)[:, None], gap_pct, np.nan)

        filled = np.where(np.isnan(gap_pct), np.inf, gap_pct)
        idx = filled.argmin(axis=1)                      # nearest line per bar
        rows = np.arange(len(df))
        nearest_val = np.where(np.isfinite(bb), pv[rows, idx], np.nan)
        nearest_name = np.where(
            np.isfinite(bb), np.asarray(PIVOT_NAMES, dtype=object)[idx], ""
        )

        df["PIV_NAME"] = nearest_name
        df["PIV_VAL"] = nearest_val
        df["GAP_PCT"] = gap_pct[rows, idx]

        open_arr = df["Open"].to_numpy(dtype=float)
        close_arr = df["Close"].to_numpy(dtype=float)
        safe_bb = np.where(bb > 0, bb, np.nan)
        # Open distance from the band (any side — "near" means near)
        df["OPEN_DIST_PCT"] = np.abs(open_arr - safe_bb) / np.abs(safe_bb) * 100.0
        # Close position vs band: negative = below the band (washout depth)
        df["PEN_PCT"] = (close_arr - safe_bb) / np.abs(safe_bb) * 100.0
        return df

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------
    def buy_signal(self, df: pd.DataFrame) -> pd.Series:
        """Idea 1 AND Idea 2 on the same bar.

        NaN comparisons evaluate to False, so warm-up rows stay False.
        """
        cfg = self.cfg
        confluence = df["GAP_PCT"] <= cfg.prox_pct          # <= 0.5%
        open_near = df["OPEN_DIST_PCT"] <= cfg.open_near_pct  # <= 0.5%
        washout = df["Close"] < df["BB_LO"]                  # closes BELOW band
        return (confluence & open_near & washout).astype(bool)

    def sell_signal(self, df: pd.DataFrame) -> pd.Series:
        """Long-only test setup: never fires (see module docstring)."""
        return pd.Series(False, index=df.index)

    # ------------------------------------------------------------------
    # Optional engine hooks (feed the Zone break / Dir / Stop columns)
    # ------------------------------------------------------------------
    def detect_zone_breakout(self, df: pd.DataFrame) -> dict:
        if bool(self.buy_signal(df).iloc[-1]):
            return {
                "broke_out": True,
                "direction": "LONG",
                "stop_loss": float(df["Low"].iloc[-1]),
            }
        return {"broke_out": False, "direction": None, "stop_loss": None}

    # ------------------------------------------------------------------
    # Display / ranking
    # ------------------------------------------------------------------
    def format_metrics(self, df: pd.DataFrame) -> str:
        last = df.iloc[-1]
        gap = last["GAP_PCT"]
        if pd.isna(gap):
            return "warm-up: no BB/pivot values yet"

        fired = bool(self.buy_signal(df).iloc[-1])
        name = str(last["PIV_NAME"]) or "?"
        bb = float(last["BB_LO"])
        piv = float(last["PIV_VAL"])
        pen = float(last["PEN_PCT"])         # % vs band, negative = below
        open_d = float(last["OPEN_DIST_PCT"])
        return (
            f"{'SETUP' if fired else 'no setup'} | {name} \u20b9{_f(piv, 1)} vs "
            f"BB_lo \u20b9{_f(bb, 1)} gap {gap:.2f}% (<= {self.cfg.prox_pct}%) | "
            f"O {open_d:.2f}% from band | C {pen:+.2f}% vs band | "
            f"stop \u20b9{_f(float(last['Low']), 1)} | "
            f"T1 mid \u20b9{_f(float(last['BB_MID']), 1)}"
        )

    def sort_value(self, df: pd.DataFrame) -> float:
        """Tighter confluence ranks higher; deeper washout breaks ties."""
        last = df.iloc[-1]
        gap = last["GAP_PCT"]
        if pd.isna(gap):
            return -999.0
        pen = last["PEN_PCT"]
        depth = 0.0 if pd.isna(pen) else min(max(0.0, -float(pen)), 2.0)
        return round(1.0 / (float(gap) + 0.01) + depth, 4)


class BBPivotWashoutDaily(BBPivotWashout):
    """1D — the idea exactly as drawn: daily setup + washout trigger."""

    @property
    def name(self) -> str:
        return "BB-Pivot Washout"

    @property
    def key(self) -> str:
        return "bb_pivot_washout"


class BBPivotWashout60(BBPivotWashout):
    """60m — same rules on hourly bars, with the previous SESSION's
    daily pivots as the shelf (intraday entry-timing view)."""

    def __init__(self, cfg: BBPivotConfig = None):
        super().__init__(cfg or BBPivotConfig(pivot_source="prev_day"))

    @property
    def name(self) -> str:
        return "BB-Pivot Washout 60"

    @property
    def key(self) -> str:
        return "bb_pivot_washout_60"

    @property
    def timeframe(self) -> str:
        return "60min"

    @property
    def min_bars(self) -> int:
        return 30
