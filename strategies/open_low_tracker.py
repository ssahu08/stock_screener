# Monthly Open=Low review table — owner's brief, point 2
# =======================================================
#   "since 1st daily candle of the month if it is Open=Low on (1D).
#    We should save it for a review table since that stock would
#    potentially be a candidate for bull trend in that month."
#
# Data lives in the `monthly_open_low` DuckDB table (created by
# storage.db.init_db / _ensure_schema). One row per symbol per month:
#   ACTIVE  — first candle O==L (candidate; metrics refresh every run)
#   CHECKED — first candle evaluated, not O==L (no refetch this month)
#   CLOSED  — month is over, kept for review history
#
# Capture happens automatically once per calendar month after a Nifty-50
# scan (app hook) or on demand from the 🕯️ Open=Low tab (Refresh button).
# Every refresh also pulls ACTIVE rows' price forward (high/low/close
# since entry) so the review table stays live all month.

import datetime
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)


# ------------------------------------------------------------------
# Symbols
# ------------------------------------------------------------------

def clean_symbol(sym: str) -> str:
    """'NSE:RELIANCE-EQ' / 'RELIANCE.NS' / 'RELIANCE' -> 'RELIANCE'."""
    s = str(sym).strip().upper()
    if s.startswith("NSE:"):
        s = s[4:]
    if s.endswith("-EQ"):
        s = s[:-3]
    if s.endswith(".NS"):
        s = s[:-3]
    return s


def fyers_symbol(sym: str) -> str:
    return f"NSE:{clean_symbol(sym)}-EQ"


# ------------------------------------------------------------------
# First-candle evaluation (pure — unit tested)
# ------------------------------------------------------------------

def evaluate_first_day(
    df: Optional[pd.DataFrame], month: datetime.date
) -> Tuple[bool, Optional[dict]]:
    """Does month's FIRST daily candle have exact Open == Low?

    Returns (checked, candidate):
      checked=False  — df has no bars for `month` yet (retry later)
      checked=True, candidate=dict  — first candle was O==L
      checked=True, candidate=None  — evaluated, not a candidate
    """
    if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return False, None
    period = pd.Period(month, freq="M")
    in_month = df[df.index.to_period("M") == period]
    if in_month.empty:
        return False, None
    first = in_month.iloc[0]
    if float(first["Open"]) == float(first["Low"]):
        entry_close = float(first["Close"])
        return True, {
            "entry_date": in_month.index[0].date(),
            "entry_open": float(first["Open"]),
            "entry_low": float(first["Low"]),
            "entry_close": entry_close,
        }
    return True, None


def _metrics_since(df: pd.DataFrame, entry_date: datetime.date,
                   entry_close: float) -> dict:
    """Close / high / low since entry + return percentages."""
    seg = df[df.index >= pd.Timestamp(entry_date)]
    if seg.empty:
        seg = df.tail(1)
    high = float(seg["High"].max())
    low = float(seg["Low"].min())
    close = float(df["Close"].iloc[-1])
    base = entry_close if entry_close > 0 else close
    return {
        "close_price": round(close, 2),
        "high_since": round(high, 2),
        "low_since": round(low, 2),
        "current_return_pct": round((close - base) / base * 100, 2),
        "max_return_pct": round((high - base) / base * 100, 2),
        "max_drawdown_pct": round((low - base) / base * 100, 2),
    }


# ------------------------------------------------------------------
# DB access
# ------------------------------------------------------------------

def _table(con) -> str:
    return "monthly_open_low"


def get_monthly_table(
    month: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """ACTIVE candidates for `month` (default: current), best return first."""
    from storage.db import get_connection

    if month is None:
        month = datetime.date.today().replace(day=1)
    con = get_connection()
    try:
        return con.execute(
            f"""
            SELECT symbol, entry_date, entry_close AS entry_price,
                   close_price, current_return_pct, max_return_pct,
                   max_drawdown_pct, status, updated_at
            FROM {_table(con)}
            WHERE trade_month = ? AND status = 'ACTIVE'
            ORDER BY current_return_pct DESC
            """,
            [month],
        ).df()
    finally:
        con.close()


# ------------------------------------------------------------------
# Refresh / capture
# ------------------------------------------------------------------

def refresh_tracker(
    symbols: List[str],
    fyers,
    log=print,
) -> dict:
    """Capture this month's 1st-of-month O==L candidates + refresh prices.

    `symbols` may be clean tickers or NSE:…-EQ strings; ACTIVE rows from
    the table are always included (their prices must update). Idempotent —
    already-CHECKED symbols are not refetched until the month rolls over.
    """
    from concurrent.futures import as_completed

    from core.data import fetch_historical_data
    from storage.db import get_connection

    now = datetime.datetime.now()
    month = now.date().replace(day=1)

    con = get_connection()
    summary = {"month": month.isoformat(), "checked": 0, "candidates": 0,
               "refreshed": 0, "failed": 0, "already_done": 0}
    try:
        # 1) Previous months' candidates are history now.
        con.execute(
            """
            UPDATE monthly_open_low SET status = 'CLOSED', updated_at = ?
            WHERE status = 'ACTIVE' AND trade_month < ?
            """,
            [now, month],
        )

        # 2) Current-month bookkeeping.
        rows = con.execute(
            "SELECT symbol, status FROM monthly_open_low WHERE trade_month = ?",
            [month],
        ).fetchall()
        tracked = {r[0] for r in rows}
        active = {r[0] for r in rows if r[1] == "ACTIVE"}

        # 3) Fetch set = unchecked symbols + ACTIVE rows (price refresh).
        wanted: List[str] = []
        seen = set()
        for s in list(symbols) + sorted(active):
            c = clean_symbol(s)
            if c in seen:
                continue
            seen.add(c)
            if c not in tracked or c in active:
                wanted.append(c)
        summary["already_done"] = len(seen) - len(wanted)

        if wanted:
            def _one(sym: str):
                try:
                    df = fetch_historical_data(
                        fyers, fyers_symbol(sym), days=365, resolution="1D"
                    )
                except Exception:
                    df = None
                return sym, df

            # 4) Fetch (same 4-worker pattern as the scan engine).
            fetched: Dict[str, Optional[pd.DataFrame]] = {}
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(_one, s) for s in wanted]
                for fut in as_completed(futures):
                    sym, df = fut.result()
                    fetched[sym] = df

            # 5) Process.
            for sym, df in sorted(fetched.items()):
                if df is None or df.empty:
                    summary["failed"] += 1
                    continue
                if sym in active:
                    _refresh_active(con, sym, month, df, now)
                    summary["refreshed"] += 1
                    continue
                checked, cand = evaluate_first_day(df, month)
                if not checked:
                    summary["failed"] += 1  # month not in data — retry later
                    continue
                summary["checked"] += 1
                if cand is None:
                    _insert(con, sym, month, None, None, now)
                else:
                    metrics = _metrics_since(df, cand["entry_date"],
                                             cand["entry_close"])
                    _insert(con, sym, month, cand, metrics, now)
                    summary["candidates"] += 1
                    log(f"🕯️  {sym}: 1st-of-month candle O==L → {month:%Y-%m} candidate")

        con.commit()
    finally:
        con.close()

    # Read AFTER closing: DuckDB allows a single writer connection at a time.
    summary["table"] = get_monthly_table(month)
    return summary


def _refresh_active(con, sym: str, month: datetime.date,
                    df: pd.DataFrame, now: datetime.datetime) -> None:
    row = con.execute(
        """
        SELECT entry_date, entry_close FROM monthly_open_low
        WHERE symbol = ? AND trade_month = ?
        """,
        [sym, month],
    ).fetchone()
    if row is None:
        return
    entry_date, entry_close = row
    if isinstance(entry_date, str):
        entry_date = datetime.date.fromisoformat(entry_date)
    m = _metrics_since(df, entry_date, float(entry_close))
    con.execute(
        """
        UPDATE monthly_open_low
        SET close_price = ?, high_since = ?, low_since = ?,
            current_return_pct = ?, max_return_pct = ?,
            max_drawdown_pct = ?, updated_at = ?
        WHERE symbol = ? AND trade_month = ?
        """,
        [m["close_price"], m["high_since"], m["low_since"],
         m["current_return_pct"], m["max_return_pct"],
         m["max_drawdown_pct"], now, sym, month],
    )


def _insert(con, sym: str, month: datetime.date,
            cand: Optional[dict], metrics: Optional[dict],
            now: datetime.datetime) -> None:
    if cand is not None:
        status = "ACTIVE"
        entry_date = cand["entry_date"]
        e_open, e_low, e_close = (cand["entry_open"], cand["entry_low"],
                                  cand["entry_close"])
        m = metrics or {}
    else:
        status = "CHECKED"
        entry_date = e_open = e_low = e_close = None
        m = {}
    con.execute(
        f"""
        INSERT INTO monthly_open_low
            (symbol, trade_month, entry_date, entry_open, entry_low,
             entry_close, close_price, high_since, low_since,
             current_return_pct, max_return_pct, max_drawdown_pct,
             status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (symbol, trade_month) DO NOTHING
        """,
        [sym, month, entry_date, e_open, e_low, e_close,
         m.get("close_price"), m.get("high_since"), m.get("low_since"),
         m.get("current_return_pct"), m.get("max_return_pct"),
         m.get("max_drawdown_pct"), status, now, now],
    )
