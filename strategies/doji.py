"""
DOJI Candle Watch — informational test strategy
================================================
Spec: docs/strategy-ideas.md §2 (owner brief 27-Sep-2026 + clarifications 28-Sep-2026).

TEST ONLY — deliberately NOT registered in strategies/registry.py and NOT wired
into app.py / core/scheduler.py (owner: "No need to add to prod application";
cloud deploys `main`, so this lives only on the feature branch and the
standalone runner tools/doji_scanner.py).

Rule (owner decision 28-Sep-2026; calibration against the owner's yellow-marked
10-min chart examples still pending):
    DOJI = |Close - Open| <= 10% of (High - Low)

Context tags (owner: "tag, not filter" — every doji is listed, context is extra):
    📉 lower-BB  Low <= BB(20, 2.0) lower band (owner's chart settings)
    🛟 near-Sx   Low within ±support_prox_pct of a standard support line
                 (P / S1 / S2 / S3). Intraday frames use the PREVIOUS SESSION's
                 daily pivots — the levels watched on the owner's 10-min charts;
                 1D/1W/1M frames use the previous candle's pivots.

Timeframes (handled by the scanner, detection is frame-agnostic):
    10min  intraday, live scan every N minutes, catch-up semantics
    60min / 1D / 1W / 1M  EOD; 1W & 1M evaluate the LAST COMPLETED candle only.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class DojiConfig:
    """Detection parameters (see module docstring for provenance)."""

    body_max_pct: float = 10.0     # body as % of range — owner choice 28-Sep
    min_range_atr: float = 0.0     # min range = k * ATR(14); 0 = disabled
                                   # (enable only if calibration shows flat-candle noise)
    bb_len: int = 20
    bb_std: float = 2.0
    support_prox_pct: float = 0.5  # same tolerance as bb_pivot_reversion confluence


# ----------------------------------------------------------------------
# Core rule
# ----------------------------------------------------------------------

def body_range_ratio(df: pd.DataFrame) -> pd.Series:
    """|Close - Open| / (High - Low). NaN when the range is 0 (no candle)."""
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    return (df["Close"] - df["Open"]).abs() / rng


def is_doji(df: pd.DataFrame, cfg: DojiConfig = None) -> pd.Series:
    """Boolean Series: body within cfg.body_max_pct% of the candle's range.

    - range == 0 (flat bar) -> False (undefined ratio; not a tradeable candle)
    - optional min_range_atr gate: range >= k * ATR(14). ATR's 14-bar warm-up
      makes early rows False — only used when the filter is enabled.
    """
    cfg = cfg or DojiConfig()
    ok = body_range_ratio(df).le(cfg.body_max_pct / 100.0)
    if cfg.min_range_atr > 0:
        rng = df["High"] - df["Low"]
        ok = ok & rng.ge(cfg.min_range_atr * atr14(df))
    return ok.fillna(False).astype(bool)


def atr14(df: pd.DataFrame) -> pd.Series:
    """ATR(14) — local copy of strategies.utils.indicators.atr semantics."""
    prev_close = df["Close"].shift(1)
    tr = np.maximum(
        df["High"] - df["Low"],
        np.maximum((df["High"] - prev_close).abs(), (df["Low"] - prev_close).abs()),
    )
    return tr.rolling(14).mean()


# ----------------------------------------------------------------------
# Context tags (owner: tag, not filter)
# ----------------------------------------------------------------------

def _support_lines(df: pd.DataFrame, pivot_source: str) -> dict:
    """{name: Series} of support-side standard pivot lines (P/S1/S2/S3).

    Reuses the validated pivot math from bb_pivot_reversion.
    """
    from strategies.bb_pivot_reversion import _pivot_lines  # local import: light startup

    lines = _pivot_lines(df, pivot_source)  # (P, R1, R2, R3, S1, S2, S3)
    return {"P": lines[0], "S1": lines[4], "S2": lines[5], "S3": lines[6]}


def add_context_tags(df: pd.DataFrame, cfg: DojiConfig = None,
                     pivot_source: str = "prev_day") -> pd.DataFrame:
    """Add columns: BB_LO, tag_lower_bb, tag_support, support_line."""
    cfg = cfg or DojiConfig()
    df = df.copy()

    mid = df["Close"].rolling(cfg.bb_len).mean()
    std = df["Close"].rolling(cfg.bb_len).std(ddof=0)
    df["BB_LO"] = mid - cfg.bb_std * std
    df["tag_lower_bb"] = (df["Low"] <= df["BB_LO"]).fillna(False).astype(bool)

    tol = cfg.support_prox_pct / 100.0
    low = df["Low"]
    best_dist = pd.Series(np.inf, index=df.index)
    best_name = pd.Series("", index=df.index, dtype=object)
    for name, level in _support_lines(df, pivot_source).items():
        dist = ((low - level) / level).abs()
        near = dist.le(tol).fillna(False)          # within ±0.5% of the line
        better = near & dist.lt(best_dist)
        best_dist = best_dist.mask(better, dist)
        best_name = best_name.mask(better, name)

    df["tag_support"] = best_name.ne("")
    df["support_line"] = best_name
    return df


# ----------------------------------------------------------------------
# Detection entry point
# ----------------------------------------------------------------------

def detect_dojis(df: pd.DataFrame, cfg: DojiConfig = None,
                 pivot_source: str = "prev_day") -> pd.DataFrame:
    """Return ONLY the doji rows, with ratio + context columns attached.

    Context (BB, pivots) is computed over the WHOLE frame first (rolling
    warm-up needs history), then rows are filtered to dojis.
    """
    cfg = cfg or DojiConfig()
    cols = ["body_rng", "tag_lower_bb", "tag_support", "support_line"]
    if df is None or df.empty:
        return pd.DataFrame(columns=list(df.columns if df is not None else
                                         ["Open", "High", "Low", "Close"]) + cols)

    out = add_context_tags(df, cfg, pivot_source)
    out["body_rng"] = body_range_ratio(out)
    out["doji"] = is_doji(out, cfg)
    return out[out["doji"]].drop(columns=["doji"])


# ----------------------------------------------------------------------
# Timeframe helpers (EOD completed-candle semantics; owner: 1W/1M completed)
# ----------------------------------------------------------------------

def filter_session(df: pd.DataFrame, day) -> pd.DataFrame:
    """Keep only bars belonging to calendar `day` (intraday catch-up scans)."""
    return df[df.index.date == day]


def completed_intraday(df: pd.DataFrame, now, bar_minutes: int) -> pd.DataFrame:
    """Keep bars whose CLOSE time has passed at `now` (never evaluate live bars).

    `now` may be tz-aware (IST) while Fyers timestamps are naive-IST —
    compare in naive land.
    """
    cutoff = pd.Timestamp(now)
    if cutoff.tzinfo is not None:
        cutoff = cutoff.tz_localize(None)
    ends = df.index + pd.Timedelta(minutes=bar_minutes)
    return df[ends <= cutoff]


def completed_dated(df: pd.DataFrame, now) -> pd.DataFrame:
    """Keep daily/weekly/monthly bars that are FULLY completed at `now`.

    A bar labeled `d` completes at 15:30 IST on `d` (daily) or at the end of
    its week/month (labels are period-end dates from the resample). So:
        label <  today                     -> completed
        label == today and time >= 15:30   -> completed (run the EOD job after close)
        label >  today                     -> in-progress (weekly/monthly label is
                                              a FUTURE period-end date)
    """
    cutoff = pd.Timestamp(now)
    today = cutoff.date()
    after_close = (cutoff.hour * 60 + cutoff.minute) >= 15 * 60 + 30
    mask = [(d < today) or (d == today and after_close) for d in df.index.date]
    return df[mask]


def resample_weekly(df_daily: pd.DataFrame) -> pd.DataFrame:
    """Daily -> weekly OHLCV with a DatetimeIndex (labels = week-ending Sunday).

    core.data.resample_to_weekly resets the index (Timestamp becomes a column);
    the scanner's completed-candle logic needs index dates, so re-set it here.
    """
    from core.data import resample_to_weekly

    wk = resample_to_weekly(df_daily)
    if wk.empty:
        return wk
    if not isinstance(wk.index, pd.DatetimeIndex):
        # resample_to_weekly resets the index; the datetime column may be
        # named "Timestamp" (index named) or carry a default name — pick by dtype.
        dt_cols = [c for c in wk.columns
                   if pd.api.types.is_datetime64_any_dtype(wk[c])]
        if not dt_cols:
            raise ValueError("weekly resample lost its datetime column")
        wk = wk.set_index(dt_cols[0])
        wk.index.name = "Timestamp"
    return wk


def resample_monthly(df_daily: pd.DataFrame) -> pd.DataFrame:
    """Daily -> monthly OHLCV (labels = month end). Reuses Open=Low's helper."""
    from strategies.open_low import _resample_monthly

    return _resample_monthly(df_daily)


# ----------------------------------------------------------------------
# Telegram formatting
# ----------------------------------------------------------------------

def format_doji_line(d: dict, with_date: bool = True,
                     date_fmt: str = None) -> str:
    """One bullet for a doji dict: sym, ts, close, body_rng, tags.

    date_fmt overrides the timestamp rendering per timeframe (EOD sections),
    e.g. 60m '%d-%b %H:%M', 1D '%d-%b', 1W 'wk %d-%b', 1M '%b %Y' — dated
    candles carry a midnight label, printing their time would be noise.
    """
    if date_fmt:
        when = d["ts"].strftime(date_fmt)
    elif with_date:
        when = d["ts"].strftime("%d-%b %H:%M")
    else:
        when = d["ts"].strftime("%H:%M")
    line = (f"• {d['sym']} {when} ₹{d['close']:,.2f} "
            f"| body {d['body_rng'] * 100:.0f}% of range")
    tags = []
    if d.get("tag_lower_bb"):
        tags.append("📉 lower-BB")
    if d.get("tag_support"):
        tags.append(f"🛟 near {d.get('support_line')}")
    if tags:
        line += " | " + " ".join(tags)
    return line


def chunk_messages(header: str, lines: list, footer: str = "",
                   limit: int = 3900) -> list:
    """Split (header + lines + footer) into Telegram-safe messages <= limit chars."""
    out, cur = [], []
    for ln in lines:
        trial = "\n".join([header] + cur + [ln] + ([footer] if footer else []))
        if len(trial) > limit and cur:
            out.append("\n".join([header] + cur + ([footer] if footer else [])))
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        out.append("\n".join([header] + cur + ([footer] if footer else [])))
    return out or ([header + "\n" + footer] if footer else [header])
