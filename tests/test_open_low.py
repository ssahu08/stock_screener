# Test bench — strategies/open_low.py + open_low_tracker.py
# ==========================================================
# Run:  python tests/test_open_low.py
#
# Spec: docs/strategy-ideas.md (owner's verbatim brief + agreed refinements)
# Gates per 26-Sep-2026 calibration (owner decision, measured on SBIN/Fyers):
#   * Supertrend: trend must be BULLISH — same-bar flip NOT required
#     (flip recency shown in metrics as entry-timing context)
#   * 1D/1W O==L exact; 1M wick <= 1% (monthly candles always wick)
#
# Fixtures are hand-crafted so each gate isolates cleanly. The 1D/1W
# Supertrend flip fixtures were validated against the real indicator:
#   quiet base (12 bars, ST warm-up) -> 1 huge down candle (trend -> -1)
#   -> quiet bear leg -> final candle: exact Open == Low + big rally
#   close (trend flips back to +1 on that same bar).
# The 1M fixture is rising daily data (monthly trend stays bullish).

import datetime
import os
import sys
import tempfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import pandas as pd

from strategies.open_low import OpenLow, _resample_monthly
from strategies.open_low_tracker import (
    clean_symbol,
    evaluate_first_day,
    fyers_symbol,
)

FAILURES = []
PASSED = 0


def check(name: str, cond: bool) -> None:
    global PASSED
    if cond:
        PASSED += 1
    else:
        FAILURES.append(name)
        print(f"  ✗ FAIL: {name}")


# ----------------------------------------------------------------------
# Helpers — 1D / 1W flip fixtures
# ----------------------------------------------------------------------

def flip_rows(n_quiet: int, n_bear: int):
    """Quiet -> crash -> quiet bear -> final O==L rally candle."""
    rows = []
    for _ in range(n_quiet):
        rows.append((100.0, 100.5, 99.6, 100.1))
    rows.append((100.0, 100.3, 85.0, 85.5))          # crash: trend -> -1
    c = 85.0
    for _ in range(n_bear):
        rows.append((c + 0.2, c + 0.7, c - 0.4, c))
        c -= 0.65
    o = round(rows[-1][3] - 0.45, 2)                 # final open == low
    rows.append((o, 106.0, o, 105.5))
    return rows


def daily_frame(final=None, final_volume: int = 250_000) -> pd.DataFrame:
    rows = flip_rows(12, 12)
    if final is not None:
        rows[-1] = final
    idx = pd.bdate_range("2026-01-01", periods=len(rows))
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = 100_000
    df.iloc[-1, df.columns.get_loc("Volume")] = final_volume
    return df


def weekly_frame() -> pd.DataFrame:
    rows = flip_rows(12, 12)
    idx = pd.date_range("2025-06-01", periods=len(rows), freq="W-SUN")
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = 900_000
    df.iloc[-1, df.columns.get_loc("Volume")] = 2_500_000
    return df


def bull_frame(n: int = 45) -> pd.DataFrame:
    """Steady uptrend — ST bullish since warm-up, NO flip on the last
    bar — with a final exact O==L candle. Proves the calibrated rule:
    bullish trend alone satisfies the Supertrend gate."""
    rows = []
    price = 100.0
    for i in range(n):
        o = price
        if i == n - 1:
            rows.append((o, o * 1.012, o, o * 1.01))   # O == Low, up close
        else:
            c = o * 1.005
            rows.append((o, c * 1.002, o * 0.998, c))
            price = c
    idx = pd.bdate_range("2026-02-02", periods=n)
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = 100_000
    df.iloc[-1, df.columns.get_loc("Volume")] = 250_000
    return df


def monthly_daily_frame(target_offset: int = 2,
                        current_first_ol: bool = True) -> pd.DataFrame:
    """Daily candles spanning ~14 months relative to TODAY.

    - target month (2 months back): first candle has exact Open == Low
      -> monthly setup must fire on that month's LAST daily bar.
    - every other month's first candle has a lower low (no setup).
    - the current (in-progress) month ALSO opens O==L when
      current_first_ol — it must NOT fire (month not confirmed yet).
    """
    today = datetime.date.today()
    months = []
    back = 13
    while back >= 0:
        m = today.month - back
        y = today.year
        while m <= 0:
            m += 12
            y -= 1
        months.append(datetime.date(y, m, 1))
        back -= 1
    target_month = months[-1 - target_offset]

    rows = []
    price = 100.0
    for mstart in months:
        mend = (datetime.date(mstart.year + (mstart.month == 12),
                              mstart.month % 12 + 1, 1)
                - datetime.timedelta(days=1))
        days = pd.bdate_range(mstart, end=min(mend, today))
        is_target = mstart == target_month
        is_current = mstart == months[-1]
        first_ol = is_target or (is_current and current_first_ol)
        vol = 300_000 if is_target else 100_000
        for i, d in enumerate(days):
            o = price
            if i == 0:
                if first_ol:
                    low = o                      # target: flat-bottomed
                else:
                    low = o * 0.98               # 2% wick: over 1M tolerance
                close = o * 1.01
                high = close * 1.002
            else:
                low = o * 0.998                  # stays above first open
                close = o * 1.005
                high = close * 1.002
            rows.append((d, o, high, low, close, vol))
            price = close
        price *= 1.015
    df = pd.DataFrame(rows,
                      columns=["Timestamp", "Open", "High", "Low",
                               "Close", "Volume"])
    return df.set_index("Timestamp")


# ======================================================================
# 1D — Open=Low + Supertrend flip + volume gates
# ======================================================================
print("— open_low_1d —")
s1d = OpenLow("daily")

df = daily_frame()
out = s1d.compute(df)
check("1D fixture: real ST flip on final bar",
      bool(out["flip_to_bullish"].iloc[-1]))
buy = s1d.buy_signal(out)
check("1D fires exactly on the final bar",
      bool(buy.iloc[-1]) and int(buy.sum()) == 1)
check("1D sell_signal never fires", not bool(s1d.sell_signal(out).any()))
m = s1d.format_metrics(out)
check("1D metrics: O==L + bullish ST + flip timing + fire",
      "O==L ✓" in m and "ST bull ✓" in m and "flip now" in m
      and "SETUP FIRE" in m)

# Gate: exactness (Open = Low + 0.01)
o0 = float(daily_frame().iloc[-1]["Low"])
df2 = daily_frame(final=(o0 + 0.01, 106.0, o0, 105.5))
out2 = s1d.compute(df2)
check("1D rejects Open = Low + 0.01 (exact == required)",
      not bool(s1d.buy_signal(out2).iloc[-1]))
check("1D metrics on near-miss show gap",
      "O +" in s1d.format_metrics(out2))

# Gate: volume < 50k
df3 = daily_frame(final_volume=40_000)
check("1D rejects volume below 50k",
      not bool(s1d.buy_signal(s1d.compute(df3)).iloc[-1]))

# Gate: volume below 20-bar average (base 100k, final 60k)
df4 = daily_frame(final_volume=60_000)
out4 = s1d.compute(df4)
check("1D rejects volume below rolling average",
      not bool(s1d.buy_signal(out4).iloc[-1])
      and not bool(out4["vol_ok"].iloc[-1]))

# Gate: an O==L day while trend is still bearish (no flip, stays -1)
o_st = float(daily_frame().iloc[-1]["Low"])
df5 = daily_frame(final=(o_st, o_st + 0.6, o_st, o_st + 0.5))
out5 = s1d.compute(df5)
check("1D rejects O==L day while trend still bearish",
      bool(out5["ol"].iloc[-1]) and not bool(s1d.buy_signal(out5).iloc[-1]))

# Calibrated rule: bullish TREND without a same-bar flip IS enough
df6 = bull_frame()
out6 = s1d.compute(df6)
check("1D fixture: bull frame has NO flip on final bar",
      not bool(out6["flip_to_bullish"].iloc[-1]))
check("1D fires on O==L + bullish trend WITHOUT same-bar flip",
      bool(s1d.buy_signal(out6).iloc[-1])
      and int(s1d.buy_signal(out6).sum()) == 1)
m6 = s1d.format_metrics(out6)
check("1D bull metrics show ST bull (no flip ever in window)",
      "ST bull ✓" in m6 and "(flip" not in m6 and "SETUP FIRE" in m6)

check("1D sort_value is finite", np.isfinite(float(s1d.sort_value(out))))

# ======================================================================
# 1W — same gates on the engine's weekly frame
# ======================================================================
print("— open_low_1w —")
s1w = OpenLow("weekly")
check("1W timeframe maps to engine 'weekly'", s1w.timeframe == "weekly")
wout = s1w.compute(weekly_frame())
check("1W fixture: real ST flip on final bar", bool(wout["flip_to_bullish"].iloc[-1]))
wb = s1w.buy_signal(wout)
check("1W fires exactly on the final (latest) week",
      bool(wb.iloc[-1]) and int(wb.sum()) == 1)
check("1W sell_signal never fires", not bool(s1w.sell_signal(wout).any()))

# ======================================================================
# 1M — daily frame in, monthly logic mapped back
# ======================================================================
print("— open_low_1m —")
s1m = OpenLow("monthly")
check("1M timeframe maps to engine 'daily'", s1m.timeframe == "daily")

mdf = monthly_daily_frame()
mout = s1m.compute(mdf)
mbuy = s1m.buy_signal(mout)
today = datetime.date.today()
target_month = pd.Period(datetime.date(
    today.year + ((today.month - 3) // 12), (today.month - 3) % 12 + 1, 1), "M")

target_rows = mout[mout.index.to_period("M") == target_month]
check("1M fixture has the target month", len(target_rows) >= 20)
check("1M fires ONLY on target month's last daily bar (no lookahead)",
      bool(mbuy.loc[target_rows.index[-1]])
      and int(mbuy.sum()) == 1
      and not bool(mbuy.loc[target_rows.index[:-1]].any()))
check("1M in-progress current month does NOT fire",
      not bool(mbuy.iloc[-1]))
check("1M sell_signal never fires", not bool(s1m.sell_signal(mout).any()))

# Variant A: data ends at the target month's end -> LIVE buy on last bar
slice_end = target_rows.index[-1]
outA = s1m.compute(mdf[mdf.index <= slice_end])
check("1M fires on last bar when month just completed",
      bool(s1m.buy_signal(outA).iloc[-1]))
mcut = s1m.format_metrics(outA)
check("1M metrics: setup fire + period label",
      "MONTHLY SETUP FIRE" in mcut and str(target_month) in mcut)

# Variant B: metrics mid-month mention building state
mcur = s1m.format_metrics(mout)
check("1M metrics mid-month show 'current month building'",
      "(current month building…)" in mcur)

# Variant C: 2% wick on the target month's FIRST candle (> 1% tol) -> silent
per = mdf.index.to_period("M")
t_first = mdf.index[per == target_month][0]
mdf3 = mdf.copy()
mdf3.loc[t_first, "Low"] = float(mdf3.loc[t_first, "Open"]) * 0.98
mout3 = s1m.compute(mdf3)
check("1M rejects 2% wick (over 1% tolerance)",
      not bool(s1m.buy_signal(mout3).any()))

# Variant D: 0.5% wick (<= 1% tolerance) -> still fires
mdf4 = mdf.copy()
mdf4.loc[t_first, "Low"] = float(mdf4.loc[t_first, "Open"]) * 0.995
mout4 = s1m.compute(mdf4)
mbuy4 = s1m.buy_signal(mout4)
check("1M accepts 0.5% wick (within 1% tolerance)",
      bool(mbuy4.loc[target_rows.index[-1]]) and int(mbuy4.sum()) == 1)

# Warm-up: < 3 monthly candles must not crash and must not signal
short = mdf.iloc[:30]  # ~1.5 months
sout = s1m.compute(short)
check("1M warm-up: no signal", not bool(s1m.buy_signal(sout).any()))
check("1M warm-up: metrics say warm-up", "warm-up" in s1m.format_metrics(sout))
check("1M warm-up: sort_value finite", np.isfinite(float(s1m.sort_value(sout))))

check("1M sort_value is finite", np.isfinite(float(s1m.sort_value(mout))))

# ======================================================================
# Registry wiring
# ======================================================================
print("— registry —")
from strategies.registry import get_strategy

for key, tf, name in (("open_low_1d", "daily", "Open=Low (1D)"),
                      ("open_low_1w", "weekly", "Open=Low (1W)"),
                      ("open_low_1m", "daily", "Open=Low (1M)")):
    entry = get_strategy(key)
    check(f"registry has {key}", entry is not None)
    if entry:
        check(f"{key} timeframe == {tf}", entry["timeframe"] == tf)
        check(f"{key} name == {name}", entry["name"] == name)
        check(f"{key} buy/sell/metrics callables present",
              callable(entry["buy_condition"])
              and callable(entry["sell_condition"])
              and callable(entry["metrics_formatter"]))

# ======================================================================
# Monthly tracker helpers
# ======================================================================
print("— tracker —")
check("clean_symbol: NSE form", clean_symbol("NSE:RELIANCE-EQ") == "RELIANCE")
check("clean_symbol: plain", clean_symbol("RELIANCE") == "RELIANCE")
check("clean_symbol: yfinance form", clean_symbol("RELIANCE.NS") == "RELIANCE")
check("fyers_symbol roundtrip", fyers_symbol("RELIANCE") == "NSE:RELIANCE-EQ")

idx = pd.bdate_range("2026-03-02", periods=10)
base = 100.0
rows = []
for i in range(len(idx)):
    o = base + i
    if i == 0:
        rows.append((o, o + 1.0, o, o + 0.8))          # first: O == L
    else:
        rows.append((o, o + 1.0, o - 0.3, o + 0.8))
fdf = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
fdf["Volume"] = 100_000

month = datetime.date(2026, 3, 1)
ok, cand = evaluate_first_day(fdf, month)
check("evaluate: candidate detected", ok and cand is not None
      and cand["entry_open"] == 100.0 and cand["entry_low"] == 100.0)

ok2, cand2 = evaluate_first_day(fdf, datetime.date(2026, 4, 1))
check("evaluate: month not in data -> not checked", not ok2 and cand2 is None)

fdf_wick = fdf.copy()
fdf_wick.iloc[0, fdf_wick.columns.get_loc("Low")] = 99.5
ok3, cand3 = evaluate_first_day(fdf_wick, month)
check("evaluate: first candle with wick -> checked, not candidate",
      ok3 and cand3 is None)
check("evaluate: empty df -> not checked",
      evaluate_first_day(pd.DataFrame(), month) == (False, None))
check("evaluate: None -> not checked",
      evaluate_first_day(None, month) == (False, None))

# --- DB roundtrip on an isolated DuckDB file ---------------------------
import storage.db as dbmod
from strategies.open_low_tracker import _insert, get_monthly_table

orig_path = dbmod.DB_PATH
tmpdir = tempfile.mkdtemp(prefix="ol_test_")
dbmod.DB_PATH = os.path.join(tmpdir, "test.duckdb")
try:
    con = dbmod.get_connection()   # creates monthly_open_low + unique index
    con.close()

    this_month = datetime.date.today().replace(day=1)
    now = datetime.datetime.now()
    cand = {"entry_date": datetime.date(2026, 9, 1), "entry_open": 100.0,
            "entry_low": 100.0, "entry_close": 101.5}
    metrics = {"close_price": 105.0, "high_since": 106.0, "low_since": 99.0,
               "current_return_pct": 3.45, "max_return_pct": 4.43,
               "max_drawdown_pct": -2.46}

    con = dbmod.get_connection()
    _insert(con, "TESTCO", this_month, cand, metrics, now)
    con.commit()
    _insert(con, "TESTCO", this_month, cand, metrics, now)   # duplicate
    con.commit()
    n = con.execute("SELECT count(*) FROM monthly_open_low").fetchone()[0]
    con.close()
    check("DB: duplicate insert idempotent", n == 1)

    tbl = get_monthly_table(this_month)
    check("DB: ACTIVE row returned", len(tbl) == 1
          and tbl.iloc[0]["symbol"] == "TESTCO"
          and float(tbl.iloc[0]["current_return_pct"]) == 3.45)

    # CHECKED rows never show in the review table
    con = dbmod.get_connection()
    _insert(con, "NOTCAND", this_month, None, None, now)
    con.commit()
    con.close()
    tbl2 = get_monthly_table(this_month)
    check("DB: CHECKED row hidden from review table",
          len(tbl2) == 1 and "NOTCAND" not in tbl2["symbol"].tolist())
finally:
    dbmod.DB_PATH = orig_path

# ======================================================================
# Summary
# ======================================================================
total = PASSED + len(FAILURES)
print()
if FAILURES:
    print(f"❌ {len(FAILURES)}/{total} checks FAILED:")
    for f in FAILURES:
        print("   -", f)
    sys.exit(1)
print(f"✅ {PASSED}/{total} checks passed")
