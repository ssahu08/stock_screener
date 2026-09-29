# Test bench — tools/open_low_intraday.py (Open=Low day-start intraday scan)
# Run:  python tests/test_open_low_intraday.py

import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from tools.doji_scanner import LAST_FETCH_ERR
from tools.open_low_intraday import (
    build_message,
    evaluate_first_candle,
    first_candle_done,
    format_line,
    scan_once,
)

FAILURES = []
PASSED = 0


def check(name, cond):
    global PASSED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}")


NOW = datetime.datetime(2026, 9, 29, 9, 26)           # Tue: first 10m bar done


# ---------------------------------------------------------------- builders
def mk10(rows, day="2026-09-29", extra_prev=None):
    """rows: (HH:MM, o,h,l,c,v) for `day`; optional one bar from a prior day."""
    idx, data = [], []
    if extra_prev:
        idx.append(pd.Timestamp(f"{extra_prev[0]} {extra_prev[1]}"))
        data.append(list(extra_prev[2:]))
    for t, o, h, l, c, v in rows:
        idx.append(pd.Timestamp(f"{day} {t}"))
        data.append([o, h, l, c, v])
    return pd.DataFrame(data, index=pd.DatetimeIndex(idx),
                        columns=["Open", "High", "Low", "Close", "Volume"])


def mkdaily(closes, today="2026-09-29"):
    """Daily frame ending on `today`; open = prev close, tiny wicks, vol 1M."""
    idx = pd.date_range(end=pd.Timestamp(today), periods=len(closes), freq="D")
    rows = []
    prev = float(closes[0])
    for c in closes:
        o = prev
        rows.append([o, max(o, c) * 1.002, min(o, c) * 0.998, float(c), 1_000_000])
        prev = float(c)
    return pd.DataFrame(rows, index=idx,
                        columns=["Open", "High", "Low", "Close", "Volume"])


BULL = [100 * (1.005 ** i) for i in range(60)] + [104.0]   # ST flips -1 on last bar? no:
# rising 60 bars -> daily trend BULLISH on the forming bar; last close patched below
BULL[-1] = BULL[-2] * 1.01                                 # keep the final bar rising
BEAR = [100 * (1.005 ** i) for i in range(60)] + [104.0]   # 60 up + -20% crash -> ST bear

FIRST_OK = ("09:15", 100.0, 102.0, 100.0, 101.0, 500_000)  # O == L exactly
LATER = [("09:25", 101.0, 102.5, 100.5, 102.0, 300_000)]


# ======================================================================
print("— trigger gate —")
check("09:25 first candle NOT done", not first_candle_done(
    datetime.datetime(2026, 9, 29, 9, 25)))
check("09:26 first candle done", first_candle_done(NOW))
check("custom --at honoured", first_candle_done(
    datetime.datetime(2026, 9, 29, 10, 0), "10:00")
    and not first_candle_done(datetime.datetime(2026, 9, 29, 9, 59), "10:00"))

# ======================================================================
print("— day-start rule —")
m = evaluate_first_candle(mk10([FIRST_OK] + LATER), mkdaily(BULL), NOW)
check("pass: exact O==L + daily ST bullish -> metrics", m is not None)
check("metrics carry volume / open / flip context",
      m["vol"] == 500_000 and m["open"] == 100.0
      and isinstance(m["flip"], str) and m["flip"] != "")
check("metrics: chg% vs prev close + ol_pct zero",
      abs(m["ol_pct"]) < 1e-9 and isinstance(m["chg_pct"], float))

wick = ("09:15", 100.0, 102.0, 99.5, 101.0, 500_000)       # 0.5% wick below open
check("fail: wick below open with exact rule",
      evaluate_first_candle(mk10([wick]), mkdaily(BULL), NOW) is None)
m2 = evaluate_first_candle(mk10([wick]), mkdaily(BULL), NOW, wick_pct=1.0)
check("pass: 0.5% wick allowed by --wick-pct 1", m2 is not None
      and 0.4 < m2["ol_pct"] < 0.6)

check("fail: O==L but daily ST bearish",
      evaluate_first_candle(mk10([FIRST_OK]), mkdaily(BEAR), NOW) is None)

check("fail: no 10m bar today (holiday/stale)",
      evaluate_first_candle(mk10([FIRST_OK], day="2026-09-26"),
                            mkdaily(BULL), NOW) is None)

stale = mkdaily(BULL).iloc[:-1]                            # daily ends yesterday
check("fail: daily frame without today's forming bar",
      evaluate_first_candle(mk10([FIRST_OK]), stale, NOW) is None)

check("fail: too little history for Supertrend",
      evaluate_first_candle(mk10([FIRST_OK]), mkdaily(BULL[:30]), NOW) is None)

check("fail: empty frames", evaluate_first_candle(
    pd.DataFrame(), pd.DataFrame(), NOW) is None)

# ======================================================================
print("— scan: rank / cap / delivery / fail-fast —")


def _sym_vol_df(v):
    return mk10([("09:15", 100.0, 102.0, 100.0, 101.0, v)])


sent = []


def fake_send(msg):
    sent.append(msg)
    return True


def fetch_by(mapping):
    def _f(fyers, sym, resolution, days):
        df10, dfd = mapping[sym]
        return (df10 if resolution == "10" else dfd).copy()
    return _f


daily = mkdaily(BULL)
mapping = {
    "NSE:AAA-EQ": (_sym_vol_df(100_000), daily),
    "NSE:BBB-EQ": (_sym_vol_df(300_000), daily),
    "NSE:CCC-EQ": (_sym_vol_df(200_000), daily),
}
sent.clear()
r = scan_once(None, list(mapping), NOW, fetch_fn=fetch_by(mapping),
              send_fn=fake_send, verbose=False)
msg = sent[0] if sent else ""
check("3 matches, 1 message, delivered", r["matches"] == 3
      and r["shown"] == 3 and r["delivered"] == 3 and len(sent) == 1)
check("ranked by volume desc (BBB > CCC > AAA)",
      msg.index("BBB") < msg.index("CCC") < msg.index("AAA"))
check("header + details present", "3 matches" in msg and "O==L" in msg
      and "ST bull" in msg and "1st vol" in msg)

big = {f"NSE:S{i}-EQ": (_sym_vol_df(100_000 * (i + 1)), daily) for i in range(7)}
sent.clear()
r = scan_once(None, list(big), NOW, fetch_fn=fetch_by(big),
              send_fn=fake_send, verbose=False)
check("top-5 cap: 7 matches -> 5 shown", r["matches"] == 7 and r["shown"] == 5)
check("header says 7 matches / top 5", "7 matches" in sent[0]
      and "top 5 by volume" in sent[0])

none_map = {"NSE:ZZZ-EQ": (mk10([wick]), daily)}            # wick fails exact rule
sent.clear()
r = scan_once(None, list(none_map), NOW, fetch_fn=fetch_by(none_map),
              send_fn=fake_send, verbose=False)
check("0 matches -> nothing sent, delivered 0",
      r["matches"] == 0 and r["delivered"] == 0 and not sent)

sent.clear()
r = scan_once(None, list(mapping), NOW, fetch_fn=fetch_by(mapping),
              send_fn=fake_send, no_alert=True, verbose=False)
check("--no-alert: console-only delivery, no send",
      r["delivered"] == 3 and not sent)

calls = []


def auth_fetch(fyers, sym, resolution, days):
    calls.append(sym)
    LAST_FETCH_ERR.clear()
    LAST_FETCH_ERR.update({"code": -16,
                           "message": "Could not authenticate the user"})
    return None


r = scan_once(None, ["NSE:A-EQ", "NSE:B-EQ", "NSE:C-EQ"], NOW,
              fetch_fn=auth_fetch, send_fn=fake_send, verbose=False)
check("dead session aborts after first symbol", len(calls) == 1)
check("auth abort: failures=1, no matches, no send",
      r["failures"] == 1 and r["matches"] == 0 and not sent)

# ======================================================================
print("— formatting —")
m["sym"] = "SBIN"                      # scan_once assigns this; simulate here
line = format_line(m, 1)
check("line carries sym/price/chg/vol/O==L/ST",
      line.startswith("1. ") and "500K" in line and "O==L" in line
      and "ST bull" in line)
check("build_message footer is informational",
      "informational" in " ".join(build_message([m], 1, NOW, "Nifty 50")))

# ======================================================================
print()
print(f"PASSED: {PASSED}  FAILED: {len(FAILURES)}")
if FAILURES:
    for f in FAILURES:
        print(f"  x {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
