# Open=Low — long-term candidate finder (owner's "Strategy 1")
# =============================================================
# Verbatim spec lives in docs/strategy-ideas.md (owner's brief):
#
#   Logic   : Open Price == Low Price on 1D / 1W / 1M candles
#   Entry   : best during Supertrend (10,3) bearish -> bullish change,
#             especially on the higher timeframes (1W, 1M)
#   Exit    : user's discretion (informational strategy — no auto exit,
#             sell_signal never fires)
#   Monthly : 1st daily candle of the month with O==L -> candidate for a
#             bull trend that month -> monthly_open_low review table
#             (strategies/open_low_tracker.py, rendered by the dashboard)
#
# Three registered strategies share this class:
#   open_low_1d  (1D) — engine daily frame, exact O==L
#   open_low_1w  (1W) — engine weekly frame, exact O==L
#   open_low_1m  (1M) — engine daily frame, resampled to monthly HERE,
#                       O==L with wick <= 1% (see calibration below)
#
# CALIBRATION (26-Sep-2026, owner decision — measured on SBIN/Fyers 7y):
#   * Supertrend gate: trend must be BULLISH when the candle forms;
#     the bear->bull flip is NOT required on the same candle. The
#     owner's words were "best would be during the change" — entry
#     timing guidance, not a hard gate. Measured: same-bar flip +
#     exact O==L = 0 signals in 7 years on SBIN 1D — dead rule.
#     Flip recency is SHOWN in the metrics ("flip now" / "N bars ago").
#   * 1M wick tolerance <= 1%: exact Open==Low NEVER happens on monthly
#     candles (0 of 83 months on SBIN) and the owner's own Feb-2021
#     SBIN example has a 0.83% wick. 1D/1W stay EXACT — measured fire
#     rates 2.9% of days / 2.0% of weeks (usable as-is).
#
# Volume gate (agreed): > 50,000 AND > rolling average of the frame.
# On 1W/1M the 50k floor is trivially true (aggregated volume) — the
# "above average activity" half carries the intent there. Monthly
# rolling window = 6 months (matches the production fetch, which only
# holds ~12 monthly candles — keeps replay and scan behaviour equal).
#
# Monthly confirmation: a month's candle is only actionable once the
# month has ENDED (label < today). The BUY therefore appears on the
# first scan after month-end — i.e. the morning of the 1st — which is
# exactly when the entry window opens. The review table keeps the
# candidate visible all month (owner spec point 2).

import datetime
from dataclasses import dataclass

import numpy as np
import pandas as pd

from strategies.base import Strategy
from strategies.utils.indicators import open_equals_low, supertrend

_TF_LABEL = {"daily": "1D", "weekly": "1W", "monthly": "1M"}


@dataclass
class OpenLowConfig:
    tolerance: float = 0.0        # 1D/1W: exact Open == Low (owner: exact)
    monthly_wick_tol_pct: float = 1.0   # 1M: wick <= 1% (calibration 26-Sep)
    st_period: int = 10
    st_mult: float = 3.0
    min_volume: int = 50_000
    vol_avg_period: int = 20      # monthly override = 6 (see _vol_gate)


def _resample_monthly(df_daily: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLCV -> monthly OHLCV (labels = month end)."""
    agg = {"Open": "first", "High": "max", "Low": "min",
           "Close": "last", "Volume": "sum"}
    try:
        out = df_daily.resample("ME").agg(agg)
    except ValueError:  # pandas < 2.2 uses 'M'
        out = df_daily.resample("M").agg(agg)
    return out.dropna()


class OpenLow(Strategy):
    """One implementation, three registered TFs (see module docstring)."""

    _KEYS = {"daily": "open_low_1d",
             "weekly": "open_low_1w",
             "monthly": "open_low_1m"}
    _NAMES = {"daily": "Open=Low (1D)",
              "weekly": "Open=Low (1W)",
              "monthly": "Open=Low (1M)"}

    def __init__(self, tf: str = "daily"):
        if tf not in self._KEYS:
            raise ValueError(f"unknown Open=Low timeframe: {tf!r}")
        self._tf = tf
        self._cfg = OpenLowConfig()

    # -- Strategy contract -------------------------------------------------
    @property
    def name(self) -> str:
        return self._NAMES[self._tf]

    @property
    def key(self) -> str:
        return self._KEYS[self._tf]

    @property
    def timeframe(self) -> str:
        # Engine frames: monthly rides the daily fetch (resampled here).
        return "weekly" if self._tf == "weekly" else "daily"

    @property
    def min_bars(self) -> int:
        # 1D: ST(10) + vol(20) warm-up | 1W: same on ~52 weekly bars
        # 1M: >= ~10 completed monthly candles => ~190 daily bars
        return {"daily": 22, "weekly": 24, "monthly": 190}[self._tf]

    # -- compute -----------------------------------------------------------
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        if self._tf == "monthly":
            return self._compute_monthly(df)
        return self._compute_frame(df)

    def _vol_gate(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Adds vol_ok / vol_ratio. Monthly uses a 6-month rolling window
        (20 months would blind the first 1.7y of any window, and it is
        what the production ~12-candle fetch effectively uses anyway)."""
        cfg = self._cfg
        n = len(frame)
        base = 6 if self._tf == "monthly" else cfg.vol_avg_period
        period = min(base, max(2, n // 2))
        vol_mean = frame["Volume"].rolling(period).mean()
        frame["vol_ratio"] = frame["Volume"] / vol_mean
        frame["vol_ok"] = (
            (frame["Volume"] > cfg.min_volume) & (frame["Volume"] > vol_mean)
        ).fillna(False)
        return frame

    def _compute_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        """1D / 1W: indicators straight on the engine's frame."""
        cfg = self._cfg
        df = df.copy()
        df["ol"] = open_equals_low(df, cfg.tolerance).astype(bool)
        df = supertrend(df, cfg.st_period, cfg.st_mult)
        df = self._vol_gate(df)
        # Bullish TREND (calibration: same-bar flip = 0 signals/7y).
        # supertrend notna() masks the warm-up, where trend is the
        # placeholder 1 from the indicator's first row.
        df["st_ok"] = (
            df["trend"].fillna(0).eq(1) & df["supertrend"].notna()
        )
        df["setup"] = (df["ol"] & df["vol_ok"] & df["st_ok"]).fillna(False)
        return df

    def _compute_monthly(self, daily: pd.DataFrame) -> pd.DataFrame:
        """1M: resample the daily frame, evaluate, map results back onto
        the daily bars (engine metrics / forward returns stay daily)."""
        cfg = self._cfg
        daily = daily.copy()

        # Columns the engine may read even when monthly is impossible
        for col in ("m_ol", "m_ol_pct", "m_trend", "m_flip", "m_vol_ratio",
                    "m_vol_ok", "m_setup", "m_valid", "m_period"):
            daily[col] = np.nan
        daily["m_period"] = daily["m_period"].astype("object")

        monthly = _resample_monthly(daily) if len(daily) else pd.DataFrame()
        if monthly.empty or len(monthly) < 3:
            daily["m_setup"] = False
            daily["m_valid"] = False
            return daily

        # 1M wick tolerance: <= 1% (calibration — exact never fires on
        # monthly candles; owner's Feb-2021 SBIN example = 0.83%).
        monthly["ol"] = open_equals_low(
            monthly, cfg.monthly_wick_tol_pct
        ).astype(bool)
        monthly["ol_pct"] = (
            (monthly["Open"] - monthly["Low"]) / monthly["Low"] * 100
        ).round(2)
        monthly = supertrend(monthly, cfg.st_period, cfg.st_mult)
        monthly = self._vol_gate(monthly)
        # Agreed gate: monthly trend must be bullish (flip not required
        # same-bar); flip recency is shown in the metrics.
        monthly["st_ok"] = (
            monthly["trend"].fillna(0).eq(1) & monthly["supertrend"].notna()
        )
        monthly["flip"] = monthly["flip_to_bullish"].fillna(False)
        monthly["setup"] = (monthly["ol"] & monthly["vol_ok"]
                            & monthly["st_ok"]).fillna(False)
        # Confirmation: month must have ended (label < today)
        monthly["confirmed"] = monthly.index < pd.Timestamp(
            datetime.date.today()
        )

        # --- map monthly state onto each month's LAST daily bar -----------
        mper = monthly.index.to_period("M")
        daily_per = daily.index.to_period("M")
        is_last_of_month = ~pd.Series(daily_per).duplicated(keep="last").to_numpy()

        def _mapped(col: str) -> pd.Series:
            lookup = pd.Series(monthly[col].to_numpy(), index=mper)
            return daily_per.map(lookup).astype(object)

        period_lookup = pd.Series(
            monthly.index.strftime("%Y-%m").to_numpy(), index=mper
        )
        daily["m_ol"] = _mapped("ol")
        daily["m_ol_pct"] = _mapped("ol_pct")
        daily["m_trend"] = _mapped("trend")
        daily["m_flip"] = _mapped("flip")
        daily["m_vol_ratio"] = _mapped("vol_ratio")
        daily["m_vol_ok"] = _mapped("vol_ok")
        daily["m_period"] = daily_per.map(period_lookup).astype(object)
        confirmed = _mapped("confirmed")
        daily["m_valid"] = (
            confirmed.fillna(False).astype(bool)
            & is_last_of_month
        )
        daily["m_setup"] = (
            _mapped("setup").fillna(False).astype(bool)
            & daily["m_valid"]
        )
        return daily

    # -- signals -----------------------------------------------------------
    def buy_signal(self, df: pd.DataFrame) -> pd.Series:
        col = "m_setup" if self._tf == "monthly" else "setup"
        return df[col].fillna(False).astype(bool)

    def sell_signal(self, df: pd.DataFrame) -> pd.Series:
        # Long-only / informational: exit is user discretion (spec).
        return pd.Series(False, index=df.index, dtype=bool)

    # -- display -----------------------------------------------------------
    def format_metrics(self, df: pd.DataFrame) -> str:
        if self._tf == "monthly":
            return self._format_monthly(df)
        r = df.iloc[-1]
        ol_pct = (float(r["Open"]) - float(r["Low"])) / max(float(r["Low"]), 1e-9) * 100
        ol_txt = "O==L ✓" if bool(r.get("ol", False)) else f"O +{ol_pct:.2f}% > low"
        trend = r.get("trend", 0)
        if pd.isna(trend):
            st_txt = "ST n/a"
        elif int(trend) == 1:
            st_txt = "ST bull ✓"
        else:
            st_txt = "ST bear ✗"
        st_txt += self._flip_recency(df, "flip_to_bullish", "bars")
        ratio = r.get("vol_ratio", np.nan)
        ratio_txt = f"{float(ratio):.1f}×avg" if pd.notna(ratio) else "vol n/a"
        vol_txt = ratio_txt + (" ✓" if bool(r.get("vol_ok", False)) else " ✗")
        tail = " | SETUP FIRE" if bool(r.get("setup", False)) else ""
        return f"{ol_txt} | {st_txt} | {vol_txt}{tail}"

    @staticmethod
    def _flip_recency(df: pd.DataFrame, col: str, unit: str) -> str:
        """" (flip now)" / " (flip 4 bars ago)" — entry-timing context."""
        flips = np.flatnonzero(
            df[col].fillna(False).astype(bool).to_numpy()
        )
        if not len(flips):
            return ""
        ago = (len(df) - 1) - int(flips[-1])
        if ago == 0:
            return " (flip now)"
        return f" (flip {ago} {unit} ago)"

    def _format_monthly(self, df: pd.DataFrame) -> str:
        valid = df[df["m_valid"].fillna(False).astype(bool)]
        if valid.empty:
            return "1M: warm-up (needs >= 10 monthly candles)"
        r = valid.iloc[-1]
        period = r.get("m_period") or "?"
        ol_txt = ("O==L ✓" if bool(r.get("m_ol", False))
                  else f"O +{float(r.get('m_ol_pct') or 0):.2f}% > low")
        trend = r.get("m_trend", np.nan)
        if pd.isna(trend):
            st_txt = "ST n/a"
        elif int(trend) == 1:
            st_txt = "ST bull ✓"
        else:
            st_txt = "ST bear ✗"
        # flip recency across confirmed months (one row per month)
        st_txt += self._flip_recency(valid, "m_flip", "months")
        ratio = r.get("m_vol_ratio", np.nan)
        ratio_txt = f"{float(ratio):.1f}×avg" if pd.notna(ratio) else "vol n/a"
        vol_txt = ratio_txt + (" ✓" if bool(r.get("m_vol_ok", False)) else " ✗")
        tail = ""
        if bool(r.get("m_setup", False)):
            tail = " | MONTHLY SETUP FIRE"
        if not bool(df["m_valid"].fillna(False).astype(bool).iloc[-1]):
            tail += " (current month building…)"
        return f"{period}: {ol_txt} | {st_txt} | {vol_txt}{tail}"

    # -- ranking -----------------------------------------------------------
    def sort_value(self, df: pd.DataFrame) -> float:
        if self._tf == "monthly":
            valid = df[df["m_valid"].fillna(False).astype(bool)]
            if valid.empty:
                return 0.0
            r = valid.iloc[-1]
            ratio, trend, flip = (r.get("m_vol_ratio", np.nan),
                                  r.get("m_trend", 0), r.get("m_flip", False))
        else:
            r = df.iloc[-1]
            ratio, trend, flip = (r.get("vol_ratio", np.nan),
                                  r.get("trend", 0), r.get("flip_to_bullish", False))
        val = 0.0
        if pd.notna(ratio):
            val += float(ratio)
        if pd.notna(trend) and int(trend) == 1:
            val += 10.0
        if bool(flip):
            val += 5.0
        return float(val)
