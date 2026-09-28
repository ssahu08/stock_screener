# Test bench — strategies/doji.py + tools/doji_scanner.py (TEST ONLY strategy)
# =============================================================================
# Run:  python tests/test_doji.py
#
# Spec: docs/strategy-ideas.md §2 (owner brief 27-Sep-2026, clarifications
# 28-Sep-2026):
#   * DOJI = |C - O| <= 10% of (H - L)          (owner decision, inclusive)
#   * context = TAGS never filters: 📉 lower-BB, 🛟 near P/S1/S2/S3
#   * intraday 10m: catch-up scans over COMPLETED bars of TODAY only
#   * EOD: today's 60m bars + latest COMPLETED daily/weekly/monthly bar
#   * dedup: every doji reported exactly once (data/doji_state.json)
#   * Telegram delivery injectable; --no-alert / --dry-run semantics

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

from strategies.doji import (
    DojiConfig,
    add_context_tags,
    body_range_ratio,
    chunk_messages,
    completed_dated,
    completed_intraday,
    detect_dojis,
    filter_session,
    format_doji_line,
    is_doji,
    resample_monthly,
    resample_weekly,
)
from tools.doji_scanner import (
    LAST_FETCH_ERR,
    _row_dict,
    auth_error_hint,
    build_eod_message,
    build_scan_message,
    eod_once,
    fetch_frame,
    load_state,
    prune_state,
    scan_intraday_once,
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
# Helpers
# ----------------------------------------------------------------------

def bars(rows, idx):
    """rows = [O,H,L,C,...] -> OHLCV DataFrame with the given DatetimeIndex."""
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"])
    df.index = pd.DatetimeIndex(idx, name="Timestamp")
    df["Volume"] = 1000
    return df


def one(o, h, l, c):
    return bars([[o, h, l, c]], ["2026-09-28 10:25"])


IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


# ======================================================================
# 1. Core rule — body <= 10% of range (owner decision, inclusive)
# ======================================================================
print("— doji rule —")
check("body exactly 10% of range IS a doji", bool(is_doji(one(100, 105, 95, 101)).iloc[0]))
check("body 10.1% of range is NOT a doji",
      not bool(is_doji(one(100, 105, 95, 101.01)).iloc[0]))
check("zero-body candle IS a doji", bool(is_doji(one(100, 105, 95, 100)).iloc[0]))
check("zero-range (H==L) candle is NOT a doji (undefined ratio)",
      not bool(is_doji(one(100, 100, 100, 100)).iloc[0]))
check("body 50% of range is NOT a doji", not bool(is_doji(one(100, 105, 95, 105)).iloc[0]))
check("body_max_pct=25 admits the 10.1% candle",
      bool(is_doji(one(100, 105, 95, 101.01), DojiConfig(body_max_pct=25)).iloc[0]))
check("body_max_pct=5 rejects the 10% candle",
      not bool(is_doji(one(100, 105, 95, 101), DojiConfig(body_max_pct=5)).iloc[0]))
check("ratio helper matches", abs(float(body_range_ratio(one(100, 105, 95, 101)).iloc[0]) - 0.10) < 1e-12)

# --- min_range_atr gate (disabled by default; enabled only if calibration demands)
def seq_times(n, start="2026-09-28 09:15", step_min=10):
    t0 = pd.Timestamp(start)
    return [t0 + pd.Timedelta(minutes=step_min * i) for i in range(n)]


# 16 rows: rows 0-14 wide (range 2, tiny body), row 15 flat (range 0.5).
# ATR(14) at row 14 = 2.0 (row 0's TR is NaN, so warm-up ends at row 14);
# row 15's ATR ≈ 1.89 > its 0.5 range -> gated out at k=1.
n = 16
idx = seq_times(n)
rows = [[100, 101, 99, 100.01] for _ in range(15)] + [[100, 100.5, 100, 100.01]]
df_gate = bars(rows, idx)
check("min_range gate off (default): all 16 tiny-body candles are dojis",
      int(is_doji(df_gate).sum()) == 16)
check("min_range gate (k=1 ATR): flat 0.5-range candle suppressed, wide kept",
      int(is_doji(df_gate, DojiConfig(min_range_atr=1.0)).sum()) == 1
      and bool(is_doji(df_gate, DojiConfig(min_range_atr=1.0)).iloc[14]))

# ======================================================================
# 2. detect_dojis — subset + attached columns
# ======================================================================
print("— detect —")
df_d = bars(
    [[100, 105, 95, 101],      # doji (10%)
     [100, 105, 95, 105],      # not
     [200, 201, 199, 200.05]], # doji (2.5%)
    ["2026-09-28 09:25", "2026-09-28 09:35", "2026-09-28 09:45"])
det = detect_dojis(df_d)
check("detect returns only doji rows (2 of 3)", len(det) == 2)
check("detect keeps datetime index",
      isinstance(det.index, pd.DatetimeIndex) and det.index[0] == pd.Timestamp("2026-09-28 09:25"))
check("detect attaches body_rng", "body_rng" in det.columns
      and abs(det["body_rng"].iloc[0] - 0.10) < 1e-9)
check("detect attaches context columns",
      all(c in det.columns for c in ("tag_lower_bb", "tag_support", "support_line")))
check("detect on empty frame returns empty", detect_dojis(pd.DataFrame()).empty)
check("detect on None returns empty", detect_dojis(None).empty)

# ======================================================================
# 3. Context tags — lower BB (owner's settings: 20, 2.0)
# ======================================================================
print("— lower-BB tag —")
closes = [[100.0, 100.1, 99.9][i % 3] for i in range(30)]
rows_bb = [[c, c + 0.05, c - 0.05, c] for c in closes[:-1]]
c = closes[-1]
rows_bb.append([c, c + 0.1, 95.0, c])          # last bar pierces the lower band
df_bb = bars(rows_bb, seq_times(30))
tagged = add_context_tags(df_bb)
check("BB_LO computed on last bar", tagged["BB_LO"].notna().iloc[-1])
check("piercing low tags lower-BB", bool(tagged["tag_lower_bb"].iloc[-1]))
check("quiet bar inside bands NOT tagged", not bool(tagged["tag_lower_bb"].iloc[15]))
check("BB column present", "BB_LO" in tagged.columns)

# ======================================================================
# 4. Support tag — prev-session pivots (the owner's intraday chart levels)
# ======================================================================
print("— support tag —")
# Day 1 aggregates H=110 L=100 C=105 -> P=105, S1=100, S2=95, S3=90
df_sup = bars(
    [[104, 110, 100, 105],       # session 1 (also a 10% doji — first session, no pivots)
     [105, 109, 100, 105],       # session 1 close
     [101.9, 103, 100.3, 102.0], # session 2: doji 0.3% above S1  -> S1
     [98, 99, 97, 98.1],         # doji in no-man's land          -> no tag
     [96, 97, 95.1, 96.05]],     # doji 0.1% above S2             -> S2
    ["2026-09-25 09:25", "2026-09-25 09:35",
     "2026-09-28 09:25", "2026-09-28 09:35", "2026-09-28 09:45"])
sup = detect_dojis(df_sup, pivot_source="prev_day")
s28 = sup[sup.index.date == datetime.date(2026, 9, 28)]
check("3 session-2 dojis detected", len(s28) == 3)
check("doji near S1 tagged S1", s28["support_line"].iloc[0] == "S1"
      and bool(s28["tag_support"].iloc[0]))
check("doji in no-man's land untagged",
      not bool(s28["tag_support"].iloc[1]) and s28["support_line"].iloc[1] == "")
check("doji near S2 tagged S2", s28["support_line"].iloc[2] == "S2"
      and bool(s28["tag_support"].iloc[2]))
check("first session has no pivot tags",
      not bool(sup["tag_support"].iloc[0]))

# ======================================================================
# 5. Timeframe helpers — completed-candle semantics (owner: 1W/1M completed)
# ======================================================================
print("— completed-candle helpers —")
# a bar timestamped 10:15 CLOSES at 10:25; the 10:25 bar closes at 10:35
df_intra = bars([[100, 101, 99, 100]] * 2, ["2026-09-28 10:15", "2026-09-28 10:25"])
check("intraday: 10:35 bar not complete at 10:30",
      len(completed_intraday(df_intra, datetime.datetime(2026, 9, 28, 10, 30), 10)) == 1)
check("intraday: 10:35 bar complete at 10:35",
      len(completed_intraday(df_intra, datetime.datetime(2026, 9, 28, 10, 35), 10)) == 2)
check("intraday: tz-aware now tolerated (naive Fyers stamps)",
      len(completed_intraday(df_intra,
                             datetime.datetime(2026, 9, 28, 10, 35, tzinfo=IST), 10)) == 2)

df_dated = bars([[100, 101, 99, 100]] * 3,
                ["2026-09-24", "2026-09-25", "2026-09-28"])
check("dated: today excluded before 15:30",
      len(completed_dated(df_dated, datetime.datetime(2026, 9, 28, 15, 29))) == 2)
check("dated: today included from 15:30",
      len(completed_dated(df_dated, datetime.datetime(2026, 9, 28, 15, 30))) == 3)
check("dated: weekend run keeps Friday",
      len(completed_dated(df_dated, datetime.datetime(2026, 9, 26, 10, 0))) == 2)

df_wk = bars([[100, 101, 99, 100]] * 3,
             ["2026-09-18", "2026-09-25", "2026-10-02"])  # last = in-progress week
done_wk = completed_dated(df_wk, datetime.datetime(2026, 9, 28, 15, 45))
check("weekly: future period-end label excluded", len(done_wk) == 2
      and done_wk.index[-1] == pd.Timestamp("2026-09-25"))

df_sess = bars([[100, 101, 99, 100]] * 3,
               ["2026-09-28 09:25", "2026-09-28 09:35", "2026-09-29 09:25"])
check("filter_session keeps target day only",
      len(filter_session(df_sess, datetime.date(2026, 9, 28))) == 2)

# ======================================================================
# 6. Resamples (weekly label = Sunday, monthly = month end)
# ======================================================================
print("— resamples —")
dates = pd.bdate_range("2026-08-20", "2026-09-28")
df_daily = bars([[100, 106, 94, 104]] * len(dates), dates)
wk = resample_weekly(df_daily)
mo = resample_monthly(df_daily)
check("weekly has DatetimeIndex (Timestamp column re-set)",
      isinstance(wk.index, pd.DatetimeIndex) and "Timestamp" not in wk.columns)
check("weekly labels are Sundays", all(d.day_name() == "Sunday" for d in wk.index))
check("monthly labels are month ends",
      list(mo.index.strftime("%Y-%m-%d")) == ["2026-08-31", "2026-09-30"])
check("monthly OHLC agg correct",
      mo["Open"].iloc[0] == 100 and mo["Close"].iloc[0] == 104
      and mo["High"].iloc[0] == 106 and mo["Low"].iloc[0] == 94)

# ======================================================================
# 7. Formatting + chunking (Telegram 4096-char limit)
# ======================================================================
print("— formatting —")
d = {"sym": "RELIANCE", "ts": pd.Timestamp("2026-09-28 10:25"), "close": 1426.6,
     "body_rng": 0.043, "tag_lower_bb": True, "tag_support": True,
     "support_line": "S1"}
line = format_doji_line(d, with_date=False)
check("line: time + price + body%", "10:25" in line and "₹1,426.60" in line
      and "body 4%" in line)
check("line: both tags", "📉 lower-BB" in line and "🛟 near S1" in line)
check("line: date omitted intraday", "28-Sep" not in line)
check("line: date present for EOD", "28-Sep" in format_doji_line(d, with_date=True))
d2 = dict(d, tag_lower_bb=False, tag_support=False)
check("line: no tags -> no tag text",
      "📉" not in format_doji_line(d2) and "🛟" not in format_doji_line(d2))

msgs = chunk_messages("HEAD", [f"line-{i} " + "x" * 190 for i in range(30)], "FOOT")
check("chunk: splits long bodies", len(msgs) >= 2)
check("chunk: every message within Telegram limit", all(len(m) <= 3900 for m in msgs))
check("chunk: all lines preserved",
      all(f"line-{i}" in "\n".join(msgs) for i in range(30)))
check("chunk: header+footer on each", all(m.startswith("HEAD") and m.endswith("FOOT")
                                          for m in msgs))
check("chunk: empty lines -> single message", len(chunk_messages("H", [], "F")) == 1)

msg = build_scan_message([d, d2], datetime.datetime(2026, 9, 28, 10, 45), "10m", "Nifty 50")
check("scan message header", "10m" in msg[0] and "2 new" in msg[0]
      and "10:45" in msg[0] and "Nifty 50" in msg[0])
check("scan message body", "RELIANCE" in msg[0])
eod_msg = build_eod_message({"60m": [d], "1D": [], "1W": [d2], "1M": []},
                            datetime.datetime(2026, 9, 28, 15, 45), "Nifty 50")
check("eod message sections", "60m (1):" in eod_msg[0] and "1W (1):" in eod_msg[0]
      and "1D" not in eod_msg[0].split("\n")[1] and "1M (" not in eod_msg[0])
check("eod lines are timeframe-aware (no 00:00 on dated candles)",
      "wk 28-Sep" in eod_msg[0] and "28-Sep 10:25" in eod_msg[0]
      and "00:00" not in eod_msg[0])
mon_msg = build_eod_message({"1M": [d]}, datetime.datetime(2026, 9, 28, 15, 45), "Nifty 50")
check("eod 1M line renders the month, not the label day",
      "Sep 2026" in mon_msg[0] and "31-Aug" not in mon_msg[0])

# ======================================================================
# 8. State dedup + pruning
# ======================================================================
print("— state —")
now = datetime.datetime(2026, 9, 28, 12, 0)
state = {"sent": {
    "10m|NSE:X-EQ|2026-09-28 10:25": "2026-09-28",   # keep (fresh)
    "10m|NSE:X-EQ|2026-09-20 10:25": "2026-09-20",   # drop (age 8 > 3)
    "1D|NSE:X-EQ|2026-09-20": "2026-09-20",           # keep (age 8 <= 10)
    "1W|NSE:X-EQ|2026-08-01": "2026-08-01",           # drop (age 58 > 40)
    "1M|NSE:X-EQ|2026-05-31": "2026-05-31",           # keep (age 120 <= 400)
    "60m|NSE:Y-EQ|2026-09-01 10:15": "bad-date",      # drop (unparseable)
    "unknown|Z": "2026-09-28",                        # keep (unknown tf)
}}
removed = prune_state(state, now)
check("prune removes exactly the stale/corrupt keys", removed == 3
      and set(state["sent"]) == {
          "10m|NSE:X-EQ|2026-09-28 10:25", "1D|NSE:X-EQ|2026-09-20",
          "1M|NSE:X-EQ|2026-05-31", "unknown|Z"})

# ======================================================================
# 9. scan_intraday_once — catch-up, completion gate, dedup, delivery
# ======================================================================
print("— intraday scan —")
INTRA_IDX = ["2026-09-25 09:25", "2026-09-25 09:35",     # Friday context (prev-day pivots)
             "2026-09-28 09:25", "2026-09-28 09:35", "2026-09-28 10:25"]
INTRA_ROWS = [
    [104, 110, 100, 105],      # Fri: also a 10% doji — must NOT leak into Monday scans
    [105, 109, 100, 105],      # Fri
    [101.9, 103, 100.3, 102.0],  # Mon 09:25 DOJI (completed by 10:30)
    [101, 105, 100, 104],      # Mon 09:35 normal (60% body)
    [100, 103, 96, 100.1],     # Mon 10:25 DOJI (completes 10:35)
]
df_scan = bars(INTRA_ROWS, INTRA_IDX)


def fake_fetch(frame):
    def _f(fyers, sym, resolution, days):
        return frame.copy()
    return _f


sent_msgs = []


def fake_send(msg):
    sent_msgs.append(msg)
    return True


with tempfile.TemporaryDirectory() as tmp:
    sp = os.path.join(tmp, "state.json")
    st = {"sent": {}}
    now1 = datetime.datetime(2026, 9, 28, 10, 30)
    r1 = scan_intraday_once(None, ["NSE:RELIANCE-EQ"], DojiConfig(), now1, st,
                            fetch_fn=fake_fetch(df_scan), send_fn=fake_send,
                            state_path=sp, verbose=False)
    check("scan1: only today's completed doji found (Fri doji + live 10:25 excluded)",
          r1["new"] == 1 and r1["delivered"] == 1)
    check("scan1: one Telegram message sent", len(sent_msgs) == 1)
    check("scan1: message names the stock/time",
          "RELIANCE" in sent_msgs[0] and "09:25" in sent_msgs[0])
    check("scan1: state persisted", os.path.exists(sp)
          and "10m|NSE:RELIANCE-EQ|2026-09-28 09:25" in st["sent"])

    r2 = scan_intraday_once(None, ["NSE:RELIANCE-EQ"], DojiConfig(), now1, st,
                            fetch_fn=fake_fetch(df_scan), send_fn=fake_send,
                            state_path=sp, verbose=False)
    check("scan2 (same time): dedup -> 0 new, no message",
          r2["new"] == 0 and len(sent_msgs) == 1)

    now2 = datetime.datetime(2026, 9, 28, 10, 40)   # 10:25 bar now complete
    r3 = scan_intraday_once(None, ["NSE:RELIANCE-EQ"], DojiConfig(), now2, st,
                            fetch_fn=fake_fetch(df_scan), send_fn=fake_send,
                            state_path=sp, verbose=False)
    check("scan3 (later): the 10:25 doji catches up as new",
          r3["new"] == 1 and len(sent_msgs) == 2 and "10:25" in sent_msgs[1])

    # --no-alert: console mode still records (delivered == new, no send)
    st4, sent4 = {"sent": {}}, []
    r4 = scan_intraday_once(None, ["NSE:RELIANCE-EQ"], DojiConfig(), now1, st4,
                            fetch_fn=fake_fetch(df_scan),
                            send_fn=lambda m: sent4.append(m),
                            no_alert=True, state_path=sp, verbose=False)
    check("--no-alert: nothing sent but findings recorded",
          len(sent4) == 0 and r4["new"] == 1 and r4["delivered"] == 1
          and len(st4["sent"]) == 1)

    # --dry-run: no state file written
    sp5 = os.path.join(tmp, "dry.json")
    st5 = {"sent": {}}
    r5 = scan_intraday_once(None, ["NSE:RELIANCE-EQ"], DojiConfig(), now1, st5,
                            fetch_fn=fake_fetch(df_scan), send_fn=lambda m: True,
                            dry_run=True, state_path=sp5, verbose=False)
    check("--dry-run: reports but writes no state",
          r5["new"] == 1 and not os.path.exists(sp5) and st5["sent"] == {})

# fetch failures / empty data
st6 = {"sent": {}}
r6 = scan_intraday_once(None, ["NSE:A-EQ", "NSE:B-EQ"], DojiConfig(),
                        datetime.datetime(2026, 9, 28, 10, 30), st6,
                        fetch_fn=lambda *a: None, send_fn=lambda m: True,
                        state_path=os.path.join(tempfile.gettempdir(), "d6.json"),
                        verbose=False)
check("fetch failure counted, no crash", r6["new"] == 0 and r6["failures"] == 2)
r7 = scan_intraday_once(None, ["NSE:A-EQ"], DojiConfig(),
                        datetime.datetime(2026, 9, 28, 10, 30), {"sent": {}},
                        fetch_fn=lambda *a: pd.DataFrame(),
                        send_fn=lambda m: True, verbose=False,
                        state_path=os.path.join(tempfile.gettempdir(), "d7.json"))
check("empty frame handled", r7["new"] == 0)

# ======================================================================
# 10. eod_once — today's 60m + latest COMPLETED 1D/1W/1M
# ======================================================================
print("— EOD pass —")
# --- 60m frame: Friday context + today (one normal, two dojis)
sixty = bars(
    [[100, 103, 98, 102],        # Fri normal
     [102, 104, 99, 103],        # Fri
     [100, 101.5, 98.5, 100.1],  # Mon 10:15 DOJI (3.3%)
     [100, 103, 98, 102],        # Mon 11:15 normal (40%)
     [99.5, 100.5, 98.5, 99.6]], # Mon 13:15 DOJI (5%)
    ["2026-09-25 10:15", "2026-09-25 11:15",
     "2026-09-28 10:15", "2026-09-28 11:15", "2026-09-28 13:15"])

# --- daily frame: defaults not-doji; week of 21-25 -> weekly doji; today daily doji
daily_dates = pd.bdate_range("2026-08-20", "2026-09-28")
daily_rows = []
for d in daily_dates:
    if d >= pd.Timestamp("2026-09-21") and d <= pd.Timestamp("2026-09-25"):
        daily_rows.append([100, 110, 90, 100.5])   # wide week -> weekly doji (0.5/20)
    elif d == pd.Timestamp("2026-09-28"):
        daily_rows.append([100, 104, 96, 100.1])   # today's daily DOJI (0.1/8)
    else:
        daily_rows.append([100, 106, 94, 104])     # normal (33% body) — Aug NOT doji
df_daily2 = bars(daily_rows, daily_dates)

def fake_fetch_eod(fyers, sym, resolution, days):
    return {"60": sixty.copy(), "1D": df_daily2.copy()}.get(resolution)

with tempfile.TemporaryDirectory() as tmp:
    sp = os.path.join(tmp, "eod.json")
    st = {"sent": {}}
    sent_m = []
    now_eod = datetime.datetime(2026, 9, 28, 15, 45)
    r1 = eod_once(None, ["NSE:TEST-EQ"], DojiConfig(), now_eod, st,
                  fetch_fn=fake_fetch_eod, send_fn=lambda m: sent_m.append(m) or True,
                  state_path=sp, verbose=False)
    check("eod: 2x60m + today's daily + last completed week",
          r1["new"] == 4 and r1["delivered"] == 4)
    keys = set(st["sent"])
    check("eod: keys per timeframe",
          sum(k.startswith("60m|") for k in keys) == 2
          and sum(k.startswith("1D|") for k in keys) == 1
          and sum(k.startswith("1W|") for k in keys) == 1
          and sum(k.startswith("1M|") for k in keys) == 0)
    check("eod: daily key is today", "1D|NSE:TEST-EQ|2026-09-28" in keys)
    check("eod: exactly one message with sections",
          len(sent_m) == 1 and "60m (2):" in sent_m[0] and "1D (1):" in sent_m[0]
          and "1W (1):" in sent_m[0] and "1M (" not in sent_m[0])
    check("eod: monthly NOT reported (Aug bar not a doji, Sep in-progress)",
          not any(k.startswith("1M|") for k in keys))

    r2 = eod_once(None, ["NSE:TEST-EQ"], DojiConfig(), now_eod, st,
                  fetch_fn=fake_fetch_eod, send_fn=lambda m: sent_m.append(m) or True,
                  state_path=sp, verbose=False)
    check("eod rerun: dedup -> 0 new, no extra message",
          r2["new"] == 0 and len(sent_m) == 1)

# pre-close EOD run: today's daily bar must NOT count yet
with tempfile.TemporaryDirectory() as tmp:
    st3 = {"sent": {}}
    r3 = eod_once(None, ["NSE:TEST-EQ"], DojiConfig(),
                  datetime.datetime(2026, 9, 28, 14, 0), st3,
                  fetch_fn=fake_fetch_eod, send_fn=lambda m: True,
                  state_path=os.path.join(tmp, "s3.json"), verbose=False)
    check("eod before 15:30: today's daily excluded, Fri daily used instead",
          "1D|NSE:TEST-EQ|2026-09-28" not in st3["sent"]
          and "1D|NSE:TEST-EQ|2026-09-25" in st3["sent"]
          and r3["new"] == 3
          and sum(k.startswith("60m|") for k in st3["sent"]) == 1)
# At 14:00: today's daily bar not completed -> falls back to the last COMPLETED
# daily (Fri 25th, a doji in the fixture); the 10:15 60m doji is in (closes
# 11:15) but 13:15 closes 14:15 -> out; last completed week still in.

# ======================================================================
# 11. row dict label extraction
# ======================================================================
print("— row dict —")
row = _row_dict("NSE:RELIANCE-EQ", pd.Timestamp("2026-09-28 10:25"),
                pd.Series({"Close": 1426.6, "body_rng": 0.04, "tag_lower_bb": False,
                           "tag_support": False, "support_line": ""}))
check("label strips NSE: and -EQ", row["sym"] == "RELIANCE")
check("close cast to float", isinstance(row["close"], float) and row["close"] == 1426.6)

# ======================================================================
# 12. fetch_frame — Fyers intraday stamps arrive UTC, must land naive-IST
# =====================================================================
print("— fetch timezone —")


class _FakeFyers:
    def __init__(self, candles):
        self._c = candles

    def history(self, data):
        return {"s": "ok", "candles": self._c}


def _ep(iso):
    """naive-UTC ISO -> epoch seconds (deterministic, no local-tz reliance)."""
    return int(pd.Timestamp(iso).value // 10**9)


# session-open 09:15 IST = 03:45 UTC, late bar 15:25 IST = 09:55 UTC
ep1 = _ep("2026-09-28 03:45")
ep2 = _ep("2026-09-28 09:55")
intra = fetch_frame(_FakeFyers([[ep1, 100, 101, 99, 100.5, 10],
                                [ep2, 100.5, 101, 100, 101, 10]]),
                    "NSE:X-EQ", "10", 4)
check("intraday converted to naive IST (03:45 UTC -> 09:15 IST)",
      list(intra.index) == [pd.Timestamp("2026-09-28 09:15"),
                            pd.Timestamp("2026-09-28 15:25")])
# completion gate now works against wall-clock: at 10:00 IST only the first bar is done
check("completed gate honours IST after conversion",
      len(completed_intraday(intra, datetime.datetime(2026, 9, 28, 10, 0), 10)) == 1)

epd = _ep("2026-09-28 00:00")
daily = fetch_frame(_FakeFyers([[epd, 100, 101, 99, 100.5, 10]]),
                    "NSE:X-EQ", "1D", 300)
check("daily stamp untouched (date convention proven by existing tools)",
      list(daily.index) == [pd.Timestamp("2026-09-28")])
check("fetch handles empty candle payload",
      fetch_frame(_FakeFyers([]), "NSE:X-EQ", "10", 4).empty)

# ======================================================================
# 13. fetch errors are SURFACED, dead sessions abort fast (28-Sep outage)
# ======================================================================
print("— fetch errors / auth abort —")


class _ErrFyers:
    """history() returns an error payload like a dead Fyers session."""

    def __init__(self, resp):
        self._r = resp
        self.calls = 0

    def history(self, data):
        self.calls += 1
        return self._r


LAST_FETCH_ERR.clear()
check("fetch_frame: auth error -> None",
      fetch_frame(_ErrFyers({"s": "error", "code": -16,
                             "message": "Could not authenticate the user"}),
                  "NSE:X-EQ", "10", 4) is None)
check("fetch_frame: error recorded for the CLI",
      LAST_FETCH_ERR.get("code") == -16
      and "authenticate" in (LAST_FETCH_ERR.get("message") or ""))
check("auth_error_hint names the fix", "python core/auth.py" in auth_error_hint())

LAST_FETCH_ERR.clear()
LAST_FETCH_ERR.update({"code": None, "message": "boom"})
check("non-auth error -> no auth hint", auth_error_hint() == "")

check("fetch success clears the last error",
      fetch_frame(_FakeFyers([[ep1, 100, 101, 99, 100.5, 10]]),
                  "NSE:X-EQ", "10", 4) is not None and not LAST_FETCH_ERR)

with tempfile.TemporaryDirectory() as tmp:
    calls = []

    def auth_fetch(fyers, sym, resolution, days):
        calls.append(sym)
        LAST_FETCH_ERR.clear()
        LAST_FETCH_ERR.update({"code": -16,
                               "message": "Could not authenticate the user"})
        return None

    r = scan_intraday_once(None, ["NSE:A-EQ", "NSE:B-EQ", "NSE:C-EQ"],
                           DojiConfig(), datetime.datetime(2026, 9, 28, 10, 30),
                           {"sent": {}}, fetch_fn=auth_fetch, send_fn=fake_send,
                           state_path=os.path.join(tmp, "state.json"),
                           verbose=False)
    check("dead session aborts after the first symbol (no doomed sweeps)",
          len(calls) == 1)
    check("auth abort reports failures, finds nothing, no crash",
          r["new"] == 0 and r["delivered"] == 0 and r["failures"] == 1)

# ======================================================================
print()
print(f"PASSED: {PASSED}  FAILED: {len(FAILURES)}")
if FAILURES:
    for f in FAILURES:
        print(f"  ✗ {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
