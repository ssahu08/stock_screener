# DOJI scanner — TEST ONLY (feature branch; never deployed to prod)
# ================================================================
# Standalone runner for strategies/doji.py. Deliberately does NOT touch
# strategies/registry.py, app.py or core/scheduler.py (owner brief:
# "No need to add to prod application" — cloud only deploys `main`).
#
#   python tools/doji_scanner.py --once            # one 10-min scan now (default)
#   python tools/doji_scanner.py --loop            # LIVE day: 10-min scans every
#                                                  #   --interval min (default 15)
#                                                  #   09:10-15:35 IST + EOD pass
#                                                  #   (60m/1D/1W/1M) at 15:45
#   python tools/doji_scanner.py --eod             # EOD pass now (run after 15:30)
#   python tools/doji_scanner.py --once --no-alert # console only, no Telegram
#   python tools/doji_scanner.py --once --dry-run  # don't touch the state file
#   python tools/doji_scanner.py --once --body-max 5   # stricter doji rule
#
# Semantics (owner decisions 28-Sep-2026, docs/strategy-ideas.md §2):
#   * live scans at regular intervals, CATCH-UP: every doji formed since 09:15
#     is reported by the next scan (only COMPLETED 10-min bars are evaluated)
#   * Telegram on every scan that has new finds — each doji reported ONCE ever
#     (state: data/doji_state.json, pruned by age per timeframe)
#   * EOD pass: today's 60-min bars + latest COMPLETED daily/weekly/monthly bar
#   * context tags (lower-BB / near support) ride along — tags, never filters

import argparse
import datetime
import json
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
from strategies.doji import (
    DojiConfig,
    chunk_messages,
    completed_dated,
    completed_intraday,
    detect_dojis,
    format_doji_line,
    resample_monthly,
    resample_weekly,
)

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
STATE_FILE = os.path.join(BASE_DIR, "data", "doji_state.json")

# timestamp rendering per EOD section — dated candles have midnight labels
EOD_DATE_FMT = {"60m": "%d-%b %H:%M", "1D": "%d-%b",
                "1W": "wk %d-%b", "1M": "%b %Y"}

# frame -> (Fyers resolution, lookback days). Lookbacks give BB(20) + previous
# session's pivots enough context; 10-min history cap on Fyers is 100 days.
RES_BY_TF = {"10m": "10", "60m": "60", "1D": "1D"}
BAR_MIN = {"10m": 10, "60m": 60}
FETCH_DAYS = {"10m": 4, "60m": 12, "1D": 300}
DAILY_PIVOT_TF = {"10m", "60m"}          # intraday frames watch daily pivots
# dedup-state key max age (days) — old keys are pruned, never re-sent keys
MAX_AGE_DAYS = {"10m": 3, "60m": 3, "1D": 10, "1W": 40, "1M": 400}


# ----------------------------------------------------------------------
# State (dedup: each doji reported exactly once)
# ----------------------------------------------------------------------

def load_state(path: str = STATE_FILE) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            st = json.load(fh)
        if isinstance(st, dict) and isinstance(st.get("sent"), dict):
            return st
    except Exception:
        pass
    return {"sent": {}}


def save_state(state: dict, path: str = STATE_FILE) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except Exception as exc:
        print(f"[doji] state save failed: {exc}")


def prune_state(state: dict, now: datetime.datetime) -> int:
    """Drop keys older than their timeframe's max age. Returns #pruned."""
    cutoff = {tf: (now.date() - datetime.timedelta(days=d))
              for tf, d in MAX_AGE_DAYS.items()}
    removed = 0
    for key in list(state["sent"].keys()):
        tf = key.split("|", 1)[0]
        limit = cutoff.get(tf)
        if limit is None:
            continue
        try:
            stamp = datetime.date.fromisoformat(state["sent"][key])
        except Exception:
            stamp = None
        if stamp is None or stamp < limit:
            del state["sent"][key]
            removed += 1
    return removed


# ----------------------------------------------------------------------
# Fetch (no min-bar rule — early-morning scans see 1-2 bars; rate-limit backoff)
# ----------------------------------------------------------------------

def fetch_frame(fyers, symbol: str, resolution: str, days: int,
                tries: int = 4) -> pd.DataFrame:
    """Range fetch -> DataFrame (possibly empty). None = hard/rate-limit failure."""
    today = datetime.date.today()
    payload = {
        "symbol": symbol,
        "resolution": resolution,
        "date_format": "1",
        "range_from": (today - datetime.timedelta(days=days)).strftime("%Y-%m-%d"),
        "range_to": today.strftime("%Y-%m-%d"),
    }
    resp, delay = None, 4
    for _ in range(tries):
        try:
            time.sleep(0.2)
            resp = fyers.history(data=payload)
        except Exception as exc:
            resp = {"s": "error", "message": str(exc)}
        if isinstance(resp, dict) and resp.get("s") == "ok":
            candles = resp.get("candles") or []
            if not candles:
                return pd.DataFrame()
            df = pd.DataFrame(candles, columns=[
                "Timestamp", "Open", "High", "Low", "Close", "Volume"])
            df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit="s")
            df = df.set_index("Timestamp").sort_index()
            if resolution in ("10", "60"):
                # Fyers intraday stamps are UTC (the NSE session arrives as
                # 03:45-10:00) -> convert to naive IST so every wall-clock
                # comparison (completed-bar gate, session filter, message
                # times) works against the local `now`. Daily/weekly/monthly
                # stamps already carry the correct trading DATE, untouched.
                df.index = (df.index.tz_localize("UTC")
                            .tz_convert("Asia/Kolkata").tz_localize(None))
            return df[~df.index.duplicated(keep="last")]
        msg = (resp.get("message", "") if isinstance(resp, dict) else str(resp)) or ""
        if "limit" not in msg.lower():
            break
        time.sleep(delay)
        delay = min(delay * 2, 30)
    return None


# ----------------------------------------------------------------------
# Row/message builders (pure — unit-tested)
# ----------------------------------------------------------------------

def _row_dict(sym: str, ts: pd.Timestamp, row: pd.Series) -> dict:
    label = sym.split(":", 1)[-1].replace("-EQ", "")
    return {
        "sym": label,
        "ts": ts,
        "close": float(row["Close"]),
        "body_rng": float(row["body_rng"]),
        "tag_lower_bb": bool(row.get("tag_lower_bb", False)),
        "tag_support": bool(row.get("tag_support", False)),
        "support_line": row.get("support_line", ""),
    }


def build_scan_message(dois: list, now: datetime.datetime,
                       resolution: str, universe_label: str) -> str:
    header = (f"🕯️ DOJI {resolution} · {len(dois)} new · {now:%H:%M} · "
              f"{universe_label}")
    lines = [format_doji_line(d, with_date=False) for d in dois]
    footer = "(reported once — 10m DOJI test scan)"
    return chunk_messages(header, lines, footer)


def build_eod_message(sections: dict, now: datetime.datetime,
                      universe_label: str) -> str:
    header = f"🕯️ DOJI EOD · {now:%a %d-%b} · {universe_label}"
    lines = []
    for tf, items in sections.items():
        if not items:
            continue
        lines.append(f"{tf} ({len(items)}):")
        lines += [format_doji_line(d, with_date=True, date_fmt=EOD_DATE_FMT.get(tf))
                  for d in items]
    footer = "(reported once — EOD DOJI test digest)"
    return chunk_messages(header, lines, footer)


# ----------------------------------------------------------------------
# Scans
# ----------------------------------------------------------------------

def _deliver(messages: list, send_fn) -> bool:
    """Send all message chunks; True = everything delivered."""
    ok = True
    for msg in messages:
        if not send_fn(msg):
            ok = False
    return ok


def scan_intraday_once(fyers, symbols: list, cfg: DojiConfig, now: datetime.datetime,
                       state: dict, tf: str = "10m",
                       fetch_fn=None, send_fn=None,
                       no_alert: bool = False, dry_run: bool = False,
                       universe_label: str = "Nifty 50",
                       state_path: str = STATE_FILE,
                       verbose: bool = True) -> dict:
    """Catch-up intraday scan: all dojis formed today on COMPLETED bars,
    deduped against state, delivered (one message per scan) if new finds."""
    fetch_fn = fetch_fn or fetch_frame
    send_fn = send_fn or send_telegram_alert
    resolution = RES_BY_TF[tf]
    pivot_source = "prev_day" if tf in DAILY_PIVOT_TF else "prev_bar"

    new, failures = [], 0
    for sym in symbols:
        df = fetch_fn(fyers, sym, resolution, FETCH_DAYS[tf])
        if df is None:
            failures += 1
            continue
        if df.empty:
            continue
        # Context FIRST (prev-session pivots + BB warm-up need earlier bars),
        # then catch-up filter: dojis on COMPLETED bars of TODAY only.
        ctx = completed_intraday(df, now, BAR_MIN[tf])
        if ctx.empty:
            continue
        det = detect_dojis(ctx, cfg, pivot_source)
        det = det[det.index.date == now.date()]
        for ts, row in det.iterrows():
            key = f"{tf}|{sym}|{ts:%Y-%m-%d %H:%M}"
            if key not in state["sent"]:
                new.append((_row_dict(sym, ts, row), key))

    sent_ok = 0
    if new:
        delivered = True
        if not no_alert:
            messages = build_scan_message([d for d, _ in new], now, tf, universe_label)
            delivered = _deliver(messages, send_fn)
        if delivered:
            sent_ok = len(new)
            if not dry_run:
                for _, key in new:
                    state["sent"][key] = now.date().isoformat()
                prune_state(state, now)
                save_state(state, state_path)

    if verbose:
        flag = "" if failures == 0 else f"  [{failures} fetch failures]"
        print(f"[{now:%H:%M}] {tf} scan: {len(symbols)} syms | "
              f"{len(new)} new dojis | delivered={sent_ok}{flag}")
        for d, _ in new:
            print("   " + format_doji_line(d, with_date=False))
    return {"new": len(new), "delivered": sent_ok, "failures": failures}


def eod_once(fyers, symbols: list, cfg: DojiConfig, now: datetime.datetime,
             state: dict,
             fetch_fn=None, send_fn=None,
             no_alert: bool = False, dry_run: bool = False,
             universe_label: str = "Nifty 50",
             state_path: str = STATE_FILE,
             verbose: bool = True) -> dict:
    """EOD pass: today's completed 60-min dojis + the latest COMPLETED
    daily / weekly / monthly bar's doji status (owner: 1W/1M = completed)."""
    fetch_fn = fetch_fn or fetch_frame
    send_fn = send_fn or send_telegram_alert
    sections = {"60m": [], "1D": [], "1W": [], "1M": []}
    failures = 0

    def _collect(tf: str, frame: pd.DataFrame, sym: str, pivot_source: str,
                 only_date=None):
        """Latest-completed-bar semantics for 1D/1W/1M; all-of-today for 60m.
        `frame` is the full CONTEXT frame (rolling indicators + prev-bar/session
        pivots need history) — rows are filtered afterwards."""
        det = detect_dojis(frame, cfg, pivot_source)
        if only_date is not None:
            det = det[det.index.date == only_date]
        if tf == "60m":
            candidates = det
        else:
            candidates = det.iloc[[-1]] if len(det) and det.index[-1] == frame.index[-1] \
                else det.iloc[0:0]
        for ts, row in candidates.iterrows():
            stamp = f"{ts:%Y-%m-%d %H:%M}" if tf == "60m" else f"{ts:%Y-%m-%d}"
            key = f"{tf}|{sym}|{stamp}"
            if key not in state["sent"]:
                sections[tf].append((_row_dict(sym, ts, row), key))

    for sym in symbols:
        # --- 60m: today's completed hourly bars (context: last 12 days) ----
        sixty = fetch_fn(fyers, sym, "60", FETCH_DAYS["60m"])
        if sixty is None:
            failures += 1
        elif not sixty.empty:
            ctx60 = completed_intraday(sixty, now, BAR_MIN["60m"])
            if not ctx60.empty:
                _collect("60m", ctx60, sym, "prev_day", only_date=now.date())

        # --- 1D / 1W / 1M from one daily fetch --------------------------
        daily = fetch_fn(fyers, sym, "1D", FETCH_DAYS["1D"])
        if daily is None:
            failures += 1
            continue
        if daily.empty:
            continue
        comp = completed_dated(daily, now)
        if not comp.empty:
            _collect("1D", comp, sym, "prev_bar")
        for tf, weekly_monthly in (("1W", resample_weekly(daily)),
                                   ("1M", resample_monthly(daily))):
            done = completed_dated(weekly_monthly, now)
            if not done.empty:
                _collect(tf, done, sym, "prev_bar")

    total_new = sum(len(v) for v in sections.values())
    delivered = True
    if total_new and not no_alert:
        messages = build_eod_message(
            {tf: [d for d, _ in items] for tf, items in sections.items()},
            now, universe_label)
        delivered = _deliver(messages, send_fn)
    sent_ok = 0
    if total_new and delivered:
        sent_ok = total_new
        if not dry_run:
            for items in sections.values():
                for _, key in items:
                    state["sent"][key] = now.date().isoformat()
            prune_state(state, now)
            save_state(state, state_path)

    if verbose:
        counts = " | ".join(f"{tf}:{len(v)}" for tf, v in sections.items())
        flag = "" if failures == 0 else f"  [{failures} fetch failures]"
        print(f"[{now:%H:%M}] EOD pass: {counts} | delivered={sent_ok}{flag}")
        for tf, items in sections.items():
            for d, _ in items:
                print("   " + format_doji_line(d, with_date=True,
                                               date_fmt=EOD_DATE_FMT.get(tf)))
    return {"new": total_new, "delivered": sent_ok, "failures": failures}


# ----------------------------------------------------------------------
# Loop + CLI
# ----------------------------------------------------------------------

def run_loop(fyers, symbols: list, cfg: DojiConfig, args) -> None:
    interval = max(1, args.interval)
    eod_h, eod_m = (int(x) for x in args.eod_at.split(":"))
    state = load_state(args.state or STATE_FILE)
    universe_label = "Nifty 50" if args.universe == "nifty50" else args.universe
    last_slot, eod_date = -1, None
    print(f"[doji] loop started: 10m every {interval}m (09:10-15:35 IST), "
          f"EOD at {args.eod_at} (60m/1D/1W/1M), alerts {'OFF' if args.no_alert else 'ON'}")
    while True:
        now = datetime.datetime.now(IST)
        if now.weekday() < 5:                       # Mon-Fri (holidays -> 0 finds)
            minutes = now.hour * 60 + now.minute
            if 9 * 60 + 10 <= minutes <= 15 * 60 + 35:
                slot = minutes // interval
                if slot != last_slot:
                    last_slot = slot
                    try:
                        scan_intraday_once(
                            fyers, symbols, cfg, now, state, "10m",
                            no_alert=args.no_alert, dry_run=args.dry_run,
                            universe_label=universe_label,
                            state_path=args.state or STATE_FILE)
                    except Exception as exc:
                        print(f"[doji] 10m scan error: {exc}")
            if not args.no_eod and eod_date != now.date() and \
                    minutes >= eod_h * 60 + eod_m:
                eod_date = now.date()
                try:
                    eod_once(
                        fyers, symbols, cfg, now, state,
                        no_alert=args.no_alert, dry_run=args.dry_run,
                        universe_label=universe_label,
                        state_path=args.state or STATE_FILE)
                except Exception as exc:
                    print(f"[doji] EOD error: {exc}")
        time.sleep(15)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="DOJI scanner (TEST ONLY — not in prod)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="one 10m scan (default)")
    mode.add_argument("--eod", action="store_true", help="one EOD pass (60m/1D/1W/1M)")
    mode.add_argument("--loop", action="store_true",
                      help="live day: 10m every --interval min + EOD at --eod-at")
    p.add_argument("--interval", type=int, default=15, help="minutes between 10m scans")
    p.add_argument("--eod-at", default="15:45", help="HH:MM for the EOD pass (loop)")
    p.add_argument("--universe", default="nifty50")
    p.add_argument("--body-max", type=float, default=10.0,
                   help="doji body as %% of range (default 10)")
    p.add_argument("--no-alert", action="store_true", help="console only, no Telegram")
    p.add_argument("--dry-run", action="store_true", help="don't write the state file")
    p.add_argument("--no-eod", action="store_true", help="loop without the EOD pass")
    p.add_argument("--state", default=None, help="override state file path (tests)")
    args = p.parse_args(argv)

    if args.universe != "nifty50":
        print(f"universe '{args.universe}' not wired yet — Nifty 50 only for now "
              f"(Nifty-100 extension is a documented backlog item)")
        return 2
    if not (args.once or args.eod or args.loop):
        args.once = True

    from engine import get_fyers_client
    fyers = get_fyers_client()
    if fyers is None:
        print("No Fyers token — run: python core/auth.py")
        return 2

    symbols = list(dict.fromkeys(get_nifty50_symbols()))  # dedup (ONGC listed twice)
    cfg = DojiConfig(body_max_pct=args.body_max)
    label = "Nifty 50"
    now = datetime.datetime.now(IST)

    if args.loop:
        run_loop(fyers, symbols, cfg, args)
        return 0
    state = load_state(args.state or STATE_FILE)
    if args.eod:
        eod_once(fyers, symbols, cfg, now, state,
                 no_alert=args.no_alert, dry_run=args.dry_run,
                 universe_label=label, state_path=args.state or STATE_FILE)
    else:
        scan_intraday_once(fyers, symbols, cfg, now, state, "10m",
                           no_alert=args.no_alert, dry_run=args.dry_run,
                           universe_label=label, state_path=args.state or STATE_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
