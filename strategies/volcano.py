# Volcano Squeeze Strategy Family
# =================================
# Derived from the user's own chart examples (dataset/*.JPG): Bollinger
# Band compression ("squeeze") followed by an expansion-candle breakout.
#
# Example breakouts this family targets (dates from the screenshots):
#   SHAREINDIA 09/09, HINDCOPPER 29/01, AEGISVOPAK 25/06 & 03/07,
#   ONGC 02/01, ABDL 12/02, KOTAKBANK 11/04, INFY 03/12, HCLTECH 29/05
#
# Design (the "test bench"):
#   - Long-only. All variants share ONE exit rule: SELL when a recent
#     squeeze/box resolves DOWN (close < 20-SMA). That shared exit is
#     deliberate — it disarms the round-trip failures in the user's own
#     examples (HCLTECH, HINDCOPPER) and keeps entry rules comparable.
#   - Volcano Watch    : squeeze still FORMING  -> advance watchlist
#                        (the "day(s) before" tier)
#   - Volcano Blast    : squeeze -> first close above upper band + volume
#   - Coil Break       : same trigger, squeeze defined as Bollinger Bands
#                        contained inside Keltner Channels (classic method)
#   - Darvas Box       : flat 20-bar box -> close above the box high
#   - Volcano Blast 60 : Volcano Blast rules on 60-minute bars
#
# Every variant is a separate registry entry, so they can be scanned
# together (engine accepts comma-separated keys) and compared side by side.

import numpy as np
import pandas as pd

from strategies.base import Strategy


# --- shared tuning constants ---------------------------------------------
BB_LEN, BB_STD = 20, 2.0           # Bollinger Bands(20, 2.0)
SQZ_QUANTILE, SQZ_LOOKBACK = 0.20, 120  # width < own 20th pctile of 120 bars
SQZ_NEED, SQZ_WINDOW = 5, 7        # >= 5 of the last 7 bars squeezed
RANGE_PCT_MAX = 8.0                # 20-bar range <= 8% of price = "flat"
FLAT10_MAX = 6.0                   # 10-bar range <= 6% = fast flat base
VOL_MULT = 1.5                     # volume >= 1.5x its 20-bar average
BAND_POKES = 3                     # squeeze within the previous N bars
FAIL_LOOKBACK_DAILY = 10           # bars still counting as "recent squeeze"


# --- helpers ---------------------------------------------------------------

def _indicators(
    df: pd.DataFrame,
    bb_len: int = BB_LEN,
    sqz_lookback: int = SQZ_LOOKBACK,
    vol_len: int = 20,
) -> pd.DataFrame:
    """Add Bollinger / Keltner / squeeze / flatness / volume state columns.

    Window sizes are parameters so FAST variants (BB(10), 60-bar squeeze
    percentile, 10-bar volume average) can match the 4-6-session coils in
    the user's 1-hour screenshot examples — the screenshot replay showed
    daily BB(20) needs ~a month to register compression and missed them.
    """
    df = df.copy()

    mid = df["Close"].rolling(bb_len).mean()
    std = df["Close"].rolling(bb_len).std(ddof=0)
    df["BB_MID"] = mid
    df["BB_UP"] = mid + BB_STD * std
    df["BB_LO"] = mid - BB_STD * std
    df["BBW"] = (df["BB_UP"] - df["BB_LO"]) / mid * 100  # width, % of mid

    # Squeeze state: width below the 20th percentile of its own trailing
    # lookback — i.e. volatility unusually compressed for this stock.
    q20 = df["BBW"].rolling(
        sqz_lookback, min_periods=max(30, sqz_lookback // 2)
    ).quantile(SQZ_QUANTILE)
    df["SQZ"] = (df["BBW"] < q20).fillna(False).astype(bool)

    # Keltner containment squeeze: full Bollinger Band inside Keltner Channel
    prev_close = df["Close"].shift(1)
    tr = np.maximum(
        df["High"] - df["Low"],
        np.maximum(
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ),
    )
    atr10 = tr.rolling(10).mean()
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    kc_mid = tp.rolling(bb_len).mean()
    df["KCSQZ"] = (
        (df["BB_UP"] < kc_mid + 1.5 * atr10)
        & (df["BB_LO"] > kc_mid - 1.5 * atr10)
    ).fillna(False).astype(bool)

    # Volume energy vs its own average
    df["VOLMA"] = df["Volume"].rolling(vol_len).mean()
    df["VOLR"] = df["Volume"] / df["VOLMA"]

    # Flat range: trailing 20-bar high-low span as % of price
    rng20 = df["High"].rolling(20).max() - df["Low"].rolling(20).min()
    df["RANGE_PCT"] = rng20 / df["Close"] * 100
    df["FLAT"] = df["RANGE_PCT"] <= RANGE_PCT_MAX

    # Fast base: trailing 10-bar span — the coils in the screenshot set
    # last 4-6 sessions, and a 20-day window includes the pre-base
    # decline so it never reads "flat" (verified on the replay).
    rng10 = df["High"].rolling(10).max() - df["Low"].rolling(10).min()
    df["FLAT10"] = (rng10 / df["Close"] * 100) <= FLAT10_MAX

    # Darvas box defined by the PRIOR 20 bars (today excluded)
    df["BOX_TOP"] = df["High"].shift(1).rolling(20).max()
    df["BOX_BOT"] = df["Low"].shift(1).rolling(20).min()
    df["BOX_FLAT"] = (
        (df["BOX_TOP"] - df["BOX_BOT"]) / df["Close"].shift(1) * 100
    ) <= RANGE_PCT_MAX

    return df


def _persistent(mask: pd.Series) -> pd.Series:
    """True where >= SQZ_NEED of the last SQZ_WINDOW bars match."""
    return (mask.rolling(SQZ_WINDOW).sum() >= SQZ_NEED).astype(bool)


def _recent(mask: pd.Series, k: int) -> pd.Series:
    """True where the mask was True within the PREVIOUS k bars
    (today excluded — today's bar is usually the one that expanded)."""
    shifted = mask.astype(float).shift(1)
    return (shifted.rolling(k, min_periods=1).max() > 0)


def _f(val, nd: int = 2) -> float:
    """Round for display, mapping NaN/None to 0.0."""
    try:
        if val is None or pd.isna(val):
            return 0.0
        return round(float(val), nd)
    except (TypeError, ValueError):
        return 0.0


# --- strategy classes ------------------------------------------------------

class _SqueezeBase(Strategy):
    """Shared wiring: daily bars, indicator computation, shared exit rule."""

    # Bars that still count as "recent squeeze" for the shared SELL rule.
    # 60-min override ≈ 5 trading sessions of hourly bars.
    fail_lookback = FAIL_LOOKBACK_DAILY

    # Indicator windows (fast variants override) + volume-gate multiplier.
    indicator_params = {"bb_len": BB_LEN, "sqz_lookback": SQZ_LOOKBACK,
                        "vol_len": 20}
    vol_mult = VOL_MULT

    @property
    def timeframe(self) -> str:
        return "daily"

    @property
    def min_bars(self) -> int:
        # squeeze lookback + indicator warm-up
        return 150

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        return _indicators(df, **self.indicator_params)

    def _sell_below_mid(self, state: pd.Series, df: pd.DataFrame) -> pd.Series:
        """Shared exit: a recent squeeze/box resolving DOWN, i.e. the
        close breaking below the 20-SMA while the compression state was
        still active within the last `fail_lookback` bars."""
        return (
            _recent(state, self.fail_lookback)
            & (df["Close"] < df["BB_MID"])
        ).astype(bool)


class VolcanoWatch(_SqueezeBase):
    """Tier 1 — the advance watchlist (signals BEFORE the breakout).

    BUY: fast squeeze persistent (>=5 of last 7 bars) AND flat 10-bar
         base AND long bias (close >= band mid) AND still below the
         upper band (compression forming, breakout has NOT fired yet).
    SELL: squeeze resolving down (close < band mid after recent squeeze).

    Windows are the FAST set (BB(10) / 60-bar percentile / 10-bar base):
    the screenshot replay proved daily BB(20) + 20-day-flat never
    registered the user's 4-6-session coils — watch fired on 0 of them.
    """

    indicator_params = {"bb_len": 10, "sqz_lookback": 60, "vol_len": 10}

    @property
    def name(self) -> str:
        return "Volcano Watch"

    @property
    def key(self) -> str:
        return "volcano_watch"

    def buy_signal(self, df: pd.DataFrame) -> pd.Series:
        still_forming = (df["Close"] >= df["BB_MID"]) & (df["Close"] <= df["BB_UP"])
        return (_persistent(df["SQZ"]) & df["FLAT10"] & still_forming).astype(bool)

    def sell_signal(self, df: pd.DataFrame) -> pd.Series:
        return self._sell_below_mid(df["SQZ"], df)

    def format_metrics(self, df: pd.DataFrame) -> str:
        last = df.iloc[-1]
        vs_mid = (last["Close"] / last["BB_MID"] - 1) * 100 if last["BB_MID"] else 0.0
        return (
            f"Squeeze: {'YES' if bool(last['SQZ']) else 'no'} | "
            f"BBW(10): {_f(last['BBW'])}% | flat10: "
            f"{'YES' if bool(last['FLAT10']) else 'no'} | "
            f"vs mid: {vs_mid:+.1f}% | vol: {_f(last['VOLR'], 1)}x"
        )

    def sort_value(self, df: pd.DataFrame) -> float:
        """Tighter band (smaller width) = stronger — sort ascending-safe."""
        v = df["BBW"].iloc[-1]
        return -float(v) if pd.notna(v) else -999.0


class VolcanoBlast(_SqueezeBase):
    """Tier 2 — the breakout trigger (signal ON the expansion candle).

    BUY: squeeze within the previous 3 bars AND close above the upper
         band AND volume >= 1.5x its 20-bar average.
    SELL: squeeze resolving down (close < 20-SMA).
    """

    @property
    def name(self) -> str:
        return "Volcano Blast"

    @property
    def key(self) -> str:
        return "volcano_blast"

    def buy_signal(self, df: pd.DataFrame) -> pd.Series:
        return (
            _recent(df["SQZ"], BAND_POKES)
            & (df["Close"] > df["BB_UP"])
            & (df["VOLR"] >= self.vol_mult)
        ).astype(bool)

    def sell_signal(self, df: pd.DataFrame) -> pd.Series:
        return self._sell_below_mid(df["SQZ"], df)

    def format_metrics(self, df: pd.DataFrame) -> str:
        last = df.iloc[-1]
        over = (last["Close"] / last["BB_UP"] - 1) * 100 if last["BB_UP"] else 0.0
        sqz = bool(_recent(df["SQZ"], BAND_POKES).iloc[-1])
        return (
            f"vol: {_f(last['VOLR'], 1)}x | over band: {over:+.1f}% | "
            f"BBW: {_f(last['BBW'])}% | squeeze recent: {'YES' if sqz else 'no'}"
        )

    def sort_value(self, df: pd.DataFrame) -> float:
        v = df["VOLR"].iloc[-1]
        return float(v) if pd.notna(v) else 0.0


class CoilBreak(_SqueezeBase):
    """Squeeze defined the classic way: Bollinger Bands fully CONTAINED
    inside Keltner Channels for >=5 of the last 7 bars, then the same
    expansion trigger as Volcano Blast.

    BUY: persistent Keltner squeeze within the previous 3 bars AND close
         above the upper band AND volume >= 1.5x average.
    SELL: squeeze resolving down (close < 20-SMA).
    """

    @property
    def name(self) -> str:
        return "Coil Break"

    @property
    def key(self) -> str:
        return "coil_break"

    def buy_signal(self, df: pd.DataFrame) -> pd.Series:
        coil = _persistent(df["KCSQZ"])
        return (
            _recent(coil, BAND_POKES)
            & (df["Close"] > df["BB_UP"])
            & (df["VOLR"] >= self.vol_mult)
        ).astype(bool)

    def sell_signal(self, df: pd.DataFrame) -> pd.Series:
        return self._sell_below_mid(df["KCSQZ"], df)

    def format_metrics(self, df: pd.DataFrame) -> str:
        last = df.iloc[-1]
        over = (last["Close"] / last["BB_UP"] - 1) * 100 if last["BB_UP"] else 0.0
        kc_now = bool(last["KCSQZ"])
        coil = bool(_persistent(df["KCSQZ"]).iloc[-1])
        return (
            f"coil now: {'YES' if kc_now else 'no'} | coil 5/7: {'YES' if coil else 'no'} | "
            f"over band: {over:+.1f}% | vol: {_f(last['VOLR'], 1)}x"
        )

    def sort_value(self, df: pd.DataFrame) -> float:
        v = df["VOLR"].iloc[-1]
        return float(v) if pd.notna(v) else 0.0


class DarvasBox(_SqueezeBase):
    """Nicolas Darvas style: flat price box, then a close above it.

    BUY: prior-20-bar box flat (<=8% span) AND close above the box top
         AND long bias (close >= 20-SMA) AND volume >= 1.5x average.
    SELL: box resolving down (close < 20-SMA after a recent flat box).
    """

    @property
    def name(self) -> str:
        return "Darvas Box"

    @property
    def key(self) -> str:
        return "darvas_box"

    def buy_signal(self, df: pd.DataFrame) -> pd.Series:
        return (
            df["BOX_FLAT"]
            & (df["Close"] >= df["BB_MID"])
            & (df["Close"] > df["BOX_TOP"])
            & (df["VOLR"] >= self.vol_mult)
        ).astype(bool)

    def sell_signal(self, df: pd.DataFrame) -> pd.Series:
        return self._sell_below_mid(df["BOX_FLAT"], df)

    def format_metrics(self, df: pd.DataFrame) -> str:
        last = df.iloc[-1]
        top, bot = last["BOX_TOP"], last["BOX_BOT"]
        gap = (last["Close"] / top - 1) * 100 if top and not pd.isna(top) else 0.0
        return (
            f"box: {_f(bot, 1)}-{_f(top, 1)} | flat: {'YES' if bool(last['BOX_FLAT']) else 'no'} | "
            f"vs top: {gap:+.1f}% | vol: {_f(last['VOLR'], 1)}x"
        )

    def sort_value(self, df: pd.DataFrame) -> float:
        v = df["VOLR"].iloc[-1]
        return float(v) if pd.notna(v) else 0.0


class VolcanoBlast60(VolcanoBlast):
    """Volcano Blast rules on 60-minute bars — same structure as the user's
    1-hour screenshots, just scanned on hourly data.

    SELL lookback widened to ~5 sessions (40 hourly bars) so exits match
    the daily variant's horizon.
    """

    @property
    def name(self) -> str:
        return "Volcano Blast 60"

    @property
    def key(self) -> str:
        return "volcano_blast_60"

    @property
    def timeframe(self) -> str:
        return "60min"

    fail_lookback = 40  # ~5 sessions of hourly bars


class VolcanoSnap(VolcanoBlast):
    """Fast trigger variant — same rules as Volcano Blast, measured at the
    scale of the screenshot coils: BB(10), 60-bar squeeze percentile,
    10-bar volume average and a 1.2x volume gate.

    Why it exists (screenshot replay evidence): daily BB(20) needs ~a
    month to register compression, so Volcano Blast caught only the one
    long coil (SHAREINDIA 09/09) and missed the short 4-6-session ones —
    ABDL 12/02 (volume 2.06x but bbw never compressed) and INFY 03/12
    (squeeze fine, volume 1.30x vs the 1.5x gate).
    """

    indicator_params = {"bb_len": 10, "sqz_lookback": 60, "vol_len": 10}
    vol_mult = 1.2

    @property
    def name(self) -> str:
        return "Volcano Snap"

    @property
    def key(self) -> str:
        return "volcano_snap"

    @property
    def min_bars(self) -> int:
        return 100  # 60-bar squeeze percentile + warm-up
