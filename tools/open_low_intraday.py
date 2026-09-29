# Open=Low DAY-START intraday scanner — TEST ONLY (feature branch)
# ================================================================
# Standalone runner for the intraday variant of Strategy 1 (Open=Low).
# Deliberately does NOT touch strategies/registry.py, app.py or
# core/scheduler.py — cloud only deploys `main`; this lives on
# feature/bb-pivot-test until the owner approves a PR.
#
#   python tools/open_low_intraday.py --once             # run now (>= 09:26 IST)
#   python tools/open_low_intraday.py --loop             # wait for the first
#                                                        #   10-min candle, then scan
#   python tools/open_low_intraday.py --once --no-alert  # console only, no Telegram
#   python tools/open_low_intraday.py --once --dry-run   # don't send anything
#   python tools/open_low_intraday.py --once --wick-pct 1   # allow <=1% wick below open
#   python tools/open_low_intraday.py --once --top 10    # show more than top-5
#
# Rule (owner brief 29-Sep-2026, docs/strategy-ideas.md §1d):
#   * TRIGGER: the day's FIRST 10-minute candle (09:15-09:25) completed
#     -> scan fires from 09:26 IST (catch-up: --once works any time after)
#   * SIGNAL : first 10-min candle today has Open == Low (exact; --wick-pct
#     to relax) AND daily Supertrend(10,3) trend is BULLISH (calibrated gate
#     from Strategy 1: same-bar flip NOT required, recency shown in metrics)
#   * RANK   : sort by the first candle's volume, descending; top-5 shown
#   * INFO   : watchlist only — no buy/sell (same as DOJI test scans)
#   * fetch + dead-session handling shared with the DOJI scanner
#     (tools/doji_scanner.fetch_frame — single implementation, 28-Sep fix)

import argparse
import datetime
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd

from core.alerts import send_telegram_alert
from core.data import get_nifty50_symbols
from strategies.doji import chunk_messages
from strategies.utils.indicators import supertrend
# Shared fetch machinery + auth-error surfacing (fixed 28-Sep on the DOJI side):
from tools.doji_scanner import LAST_FETCH_ERR, auth_error_hint, fetch_frame

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

TRIGGER_HH, TRIGGER_MM = 9, 26   # first 10-min bar (09:15-09:25) completed at 09:25
DAILY_BARS = 300                 # Fyers daily history cap ~366; ST(10,3) context
TENM_DAYS = 2                    # today + 1 buffer day of 10-min history
MIN_DAILY_BARS = 60              # enough for a stable Supertrend(10,3)


def trigger_minutes(at: str = None) -> int:
    """HH:MM minutes-of-day for the scan trigger (default 09:26)."""
    if not at:
        return TRIGGER_HH * 60 + TRIGGER_MM
    hh, mm = (int(x) for x in at.split(":"))
    return hh * 60 + mm


def first_candle_done(now: datetime.datetime, at: str = None) -> bool:
    """True once the day's first 10-min bar (09:15-09:25) has completed."""
    return (now.hour * 60 + now.minute) >= trigger_minutes(at)


def _vol(v: float) -> str:
    v = float(v or 0)
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{v / 1_000:.0f}K"
    return f"{v:.0f}"


def evaluate_first_candle(df10: pd.DataFrame, dfd: pd.DataFrame,
                          now: datetime.datetime,
                          wick_pct: float = 0.0) -> dict:
    """Day-start rule -> metrics dict, or None when the signal doesn't fire.

    df10 : 10-min frame (IST)  — subject: TODAY's first bar, Open == Low
    dfd  : daily frame          — gate: forming daily bar's Supertrend bullish
    wick_pct: 0 = exact O==L (Strategy-1 1D/1W calibration); >0 = max % wick
              allowed below the open (e.g. 1.0 mirrors the 1M rule).
    """
    if df10 is None or dfd is None or df10.empty or dfd.empty:
        return None
    today10 = df10[df10.index.date == now.date()]
    if today10.empty:
        return None                                   # no session / stale fetch
    first = today10.iloc[0]
    o, low = float(first["Open"]), float(first["Low"])
    if low <= 0:
        return None
    ol_pct = (o - low) / o * 100.0
    if wick_pct > 0:
        if ol_pct > wick_pct:
            return None
    elif o != low:
        return None                                   # exact O==L rule
    # Daily frame must carry TODAY's forming bar — ST gates on that candle.
    if dfd.index[-1].date() != now.date():
        return None
    if len(dfd) < MIN_DAILY_BARS:
        return None
    st = supertrend(dfd, 10, 3.0)
    last = st.iloc[-1]
    if int(last["trend"]) != 1:
        return None                                   # daily trend must be BULL
    # Flip recency = entry-timing context (calibrated: flip itself not a gate)
    flip_idx = st.index[st["flip_to_bullish"].fillna(False)]
    if len(flip_idx):
        ago = (len(st) - 1) - st.index.get_loc(flip_idx[-1])
        flip = "flip now" if ago == 0 else f"flip {ago}d ago"
    else:
        flip = "no flip yet"
    prev_close = float(dfd["Close"].iloc[-2])
    close = float(dfd["Close"].iloc[-1])
    return {
        "open": o,
        "low": low,
        "close": close,
        "chg_pct": (close - prev_close) / prev_close * 100.0 if prev_close else 0.0,
        "vol": float(first["Volume"]),                 # first-candle volume = rank key
        "ol_pct": ol_pct,
        "st": float(last["supertrend"]),
        "flip": flip,
        "ts": today10.index[0],
    }


def format_line(m: dict, rank: int = None) -> str:
    pfx = f"{rank}. " if rank else ""
    return (f"{pfx}{m['sym']} {m['close']:.2f} ({m['chg_pct']:+.1f}%) · "
            f"1st vol {_vol(m['vol'])} · O==L {m['ol_pct']:.2f}% · "
            f"ST bull ({m['flip']})")


def build_message(shown: list, total: int, now: datetime.datetime,
                  universe_label: str) -> list:
    """Telegram-safe chunks (list of messages) — header repeats per chunk."""
    header = (f"🌅 OPEN=LOW day-start · {total} match"
              f"{'es' if total != 1 else ''} · {now:%d-%b %H:%M} · {universe_label}"
              + (f" (top {len(shown)} by volume)" if total > len(shown) else ""))
    lines = [format_line(m, i + 1) for i, m in enumerate(shown)]
    footer = ("(first 10-min candle of the day · sorted by volume · "
              "informational watchlist)")
    return chunk_messages(header, lines, footer)


def scan_once(fyers, symbols: list, now: datetime.datetime, *,
              wick_pct: float = 0.0, top_n: int = 5,
              fetch_fn=None, send_fn=None,
              no_alert: bool = False, dry_run: bool = False,
              universe_label: str = "Nifty 50",
              verbose: bool = True) -> dict:
    """One day-start pass: first-10m O==L + daily ST bullish, ranked by volume."""
    fetch_fn = fetch_fn or fetch_frame
    send_fn = send_fn or send_telegram_alert
    LAST_FETCH_ERR.clear()               # per-scan error context

    matches, failures = [], 0
    for sym in symbols:
        df10 = fetch_fn(fyers, sym, "10", TENM_DAYS)
        if df10 is None:
            failures += 1
            if auth_error_hint():
                break                    # dead session — skip the rest
            continue
        dfd = fetch_fn(fyers, sym, "1D", DAILY_BARS)
        if dfd is None:
            failures += 1
            if auth_error_hint():
                break
            continue
        m = evaluate_first_candle(df10, dfd, now, wick_pct)
        if m is not None:
            m["sym"] = sym.split(":", 1)[-1].replace("-EQ", "")
            matches.append(m)

    matches.sort(key=lambda m: m["vol"], reverse=True)   # highest volume first
    shown = matches[:top_n]

    delivered = 0
    if shown:
        if no_alert or dry_run:
            delivered = len(shown)       # console-only / dry-run still "reports"
        else:
            ok = True
            for chunk in build_message(shown, len(matches), now, universe_label):
                if not send_fn(chunk):
                    ok = False
            if ok:
                delivered = len(shown)

    if verbose:
        flag = "" if failures == 0 else f"  [{failures} fetch failures]"
        print(f"[{now:%H:%M}] day-start scan: {len(symbols)} syms | "
              f"{len(shown)} shown / {len(matches)} matches | "
              f"delivered={delivered}{flag}")
        if failures and auth_error_hint():
            print("      " + auth_error_hint())
        for i, m in enumerate(shown):
            print("   " + format_line(m, i + 1))
        if not matches and not failures:
            print("   (no stock matched: first-10m O==L + daily ST bullish)")
    return {"matches": len(matches), "shown": len(shown),
            "delivered": delivered, "failures": failures}


def run_loop(client_factory, symbols: list, args) -> None:
    """Waits for the first 10-min candle, then scans ONCE per day.

    `client_factory` is called on every attempt so a mid-day
    `python core/auth.py` self-heals without a restart (28-Sep lesson).
    """
    at = args.at
    fired_date, last_attempt, waiting_printed = None, -1, False
    print(f"[open-low-intraday] loop started: day-start scan at "
          f"{at or '09:26'} IST (first 10-min candle), "
          f"top {args.top}, wick<={args.wick_pct}%, "
          f"alerts {'OFF' if args.no_alert else 'ON'}")
    while True:
        now = datetime.datetime.now(IST)
        if now.weekday() < 5:                          # Mon-Fri
            minutes = now.hour * 60 + now.minute
            if fired_date != now.date() and not first_candle_done(now, at):
                if not waiting_printed:
                    waiting_printed = True
                    print(f"[{now:%H:%M}] waiting for the first 10-min candle "
                          "(09:15-09:25)…")
            elif fired_date != now.date():
                slot = minutes // 5                    # retry cadence if a try fails
                if slot != last_attempt:
                    last_attempt = slot
                    fyers = client_factory()
                    if fyers is None:
                        print(f"[{now:%H:%M}] day-start scan waiting — no valid "
                              "Fyers token; run: python core/auth.py")
                    else:
                        try:
                            scan_once(
                                fyers, symbols, now,
                                wick_pct=args.wick_pct, top_n=args.top,
                                no_alert=args.no_alert, dry_run=args.dry_run,
                                universe_label=_label(args))
                            fired_date = now.date()    # done for the day
                        except Exception as exc:
                            print(f"[open-low-intraday] scan error: {exc}")
        else:
            waiting_printed = False                    # new day, arm the notice
        time.sleep(15)


def _label(args) -> str:
    return "Nifty 50" if args.universe == "nifty50" else args.universe


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Open=Low day-start intraday scanner (TEST ONLY — not in prod)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true",
                      help="one day-start scan now (default; works after 09:26)")
    mode.add_argument("--loop", action="store_true",
                      help="live day: wait for the first 10-min candle, scan once")
    p.add_argument("--at", default=None, metavar="HH:MM",
                   help="trigger time (default 09:26 = first candle done)")
    p.add_argument("--wick-pct", type=float, default=0.0,
                   help="max %% wick below open allowed (0 = exact O==L)")
    p.add_argument("--top", type=int, default=5,
                   help="how many to show, ranked by volume (default 5)")
    p.add_argument("--universe", default="nifty50")
    p.add_argument("--no-alert", action="store_true", help="console only, no Telegram")
    p.add_argument("--dry-run", action="store_true", help="don't send anything")
    args = p.parse_args(argv)

    if args.universe != "nifty50":
        print(f"universe '{args.universe}' not wired yet — Nifty 50 only for now")
        return 2
    if not (args.once or args.loop):
        args.once = True

    from engine import get_fyers_client

    symbols = list(dict.fromkeys(get_nifty50_symbols()))   # dedup (ONGC twice)
    now = datetime.datetime.now(IST)

    if args.loop:
        # No upfront token check — attempts self-heal after `python core/auth.py`.
        run_loop(get_fyers_client, symbols, args)
        return 0

    if not first_candle_done(now, args.at):
        print(f"First 10-min candle not complete yet — the 09:15-09:25 bar "
              f"closes at 09:25, trigger {args.at or '09:26'} IST. "
              "Use --loop to wait for it.")
        return 3
    fyers = get_fyers_client()
    if fyers is None:
        print("No Fyers token — run: python core/auth.py")
        return 2
    scan_once(fyers, symbols, now, wick_pct=args.wick_pct, top_n=args.top,
              no_alert=args.no_alert, dry_run=args.dry_run,
              universe_label=_label(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
