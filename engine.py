"""
Screener Engine — main orchestration
=====================================

Workflow (Option A — login is USER-RUN only):
    1. python core/auth.py     <- you authenticate; token saved locally
    2. python engine.py        <- runs a scan (or use the Streamlit dashboard)
    3. Results -> console + DuckDB + Telegram

    Streamlit:  streamlit run app.py   (Run button calls run_screener)

Adding strategies:
    Create strategies/<name>.py subclassing Strategy,
    register it in strategies/registry.py — it appears in the menu
    and dashboard automatically.

Design notes:
    - fyersModel is imported INSIDE functions (never at module level in
      type hints) — that was the bug that broke the first draft.
    - Auth is strictly non-interactive here: if the token is missing we
      print "run python core/auth.py" and stop. No prompts, ever.
"""

import os
import sys
import argparse
import datetime
import threading
import warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# Project root on sys.path so `core`, `strategies`, `storage`, `config` resolve
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# Windows consoles default to cp1252 — emoji in logs would crash scans.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

warnings.filterwarnings("ignore")

from config.settings import UNIVERSE_SETTINGS
from config.keys import CLIENT_ID
from core.auth import read_valid_token
from core.data import (
    fetch_nse_universe,
    filter_by_price,
    fetch_historical_data,
    resample_to_weekly,
    get_nifty50_symbols,
)
from core.alerts import send_telegram_alert
from strategies.registry import get_strategy, list_strategies
from storage.db import init_db, save_scan_results

LOG_DIR = os.path.join(BASE_DIR, "logs")

# Fyers caps intraday history ranges (resolutions 1..240 min) at 100 days
SIXTY_MIN_HISTORY_DAYS = 100

# Serialize console output from worker threads
_print_lock = threading.Lock()


def _log(msg: str) -> None:
    with _print_lock:
        try:
            print(msg, flush=True)
        except UnicodeEncodeError:
            # Console may be cp1252 (Windows) — never let logging kill a scan
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(msg.encode(enc, errors="replace").decode(enc), flush=True)


# ------------------------------------------------------------------
# Fyers client (non-interactive auth)
# ------------------------------------------------------------------

def get_fyers_client():
    """Return an authenticated FyersModel, or None with instructions.

    Never prompts. Per Option A, you must first run:
        python core/auth.py
    """
    from fyers_apiv3 import fyersModel  # imported here, not at module level

    token = read_valid_token()
    if not token:
        _log("❌ No valid Fyers token for today.")
        _log("   Run this once in your terminal, then retry:")
        _log("       python core/auth.py")
        return None

    os.makedirs(LOG_DIR, exist_ok=True)
    return fyersModel.FyersModel(
        client_id=CLIENT_ID,
        is_async=False,
        token=token,
        log_path=LOG_DIR + os.sep,
    )


# ------------------------------------------------------------------
# Per-ticker evaluation
# ------------------------------------------------------------------

def _signal_row(
    strategy,
    df: pd.DataFrame,
    ticker: str,
    scan_id: str,
    scan_time: datetime.datetime,
    forward_weeks: int,
) -> Dict[str, Any]:
    """Indicators -> signal -> backtest stats for one frame.

    Returns a row with EXACTLY storage.db.SCAN_COLUMNS plus an internal
    "sort_value" (dropped by db.py).
    """
    df = strategy.compute(df)
    buy_series = strategy.buy_signal(df)
    sell_series = strategy.sell_signal(df)

    is_buy = bool(buy_series.iloc[-1])
    is_sell = bool(sell_series.iloc[-1])
    signal = "BUY" if is_buy else ("SELL" if is_sell else "HOLD")

    # --- Prices / latest-bar change -------------------------------------
    latest_close = float(df["Close"].iloc[-1])
    prev_close = float(df["Close"].iloc[-2])
    bar_change = round((latest_close - prev_close) / prev_close * 100, 2)

    # --- Historical backtest (exclude the live bar) ----------------------
    # forward_weeks is expressed in WEEKS — convert to this strategy's
    # bar count so "avg 4W fwd" stays truthful on daily / 60-min frames.
    bars_per_week = {"weekly": 1, "daily": 5, "60min": 32}.get(
        strategy.timeframe, 1
    )
    fwd_bars = forward_weeks * bars_per_week
    hist_mask = buy_series.iloc[:-1]
    fwd_ret = (
        (df["Close"].shift(-fwd_bars) - df["Close"]) / df["Close"] * 100
    )
    # Positional numpy mask (one slot per row). Label-aligned boolean
    # masks EXPLODE when a frame has duplicate index labels — Fyers
    # occasionally returns duplicate intraday timestamps (verified: 7
    # duplicated hourly candles on a 505-row 60-min frame -> mask of 543).
    eligible = np.zeros(len(df), dtype=bool)
    hm = hist_mask.to_numpy(dtype=bool)
    fwd_ok = fwd_ret.notna().to_numpy()
    eligible[: len(hm)] = hm & fwd_ok[: len(hm)]
    n_signals = int(eligible.sum())
    fwd_vals = fwd_ret.to_numpy()
    avg_ret = (
        round(float(fwd_vals[eligible].mean()), 2) if n_signals else 0.0
    )

    if n_signals:
        if "Timestamp" in df.columns:
            dates = pd.to_datetime(df.loc[eligible, "Timestamp"])
        else:
            # Daily / 60-min frames are indexed by Timestamp — wrap in a
            # Series because a DatetimeIndex has no .dt accessor.
            dates = pd.Series(pd.to_datetime(df.index[eligible]))
        date_list = dates.dt.strftime("%Y-%m-%d").tolist()
        signal_dates = ", ".join(date_list[-10:])
        if len(date_list) > 10:
            signal_dates = f"...{signal_dates}"
    else:
        signal_dates = ""

    # --- Zone breakout / profit warning (optional strategy hooks) --------
    zone = {}
    zone_fn = getattr(strategy, "detect_zone_breakout", None)
    if callable(zone_fn):
        zone = zone_fn(df) or {}

    warn = False
    warn_fn = getattr(strategy, "profit_taking_warning", None)
    if callable(warn_fn):
        warn = bool(warn_fn(df))

    clean_ticker = ticker.replace("NSE:", "").replace("-EQ", "")

    # Row keys inserted in SCAN_COLUMNS order (also enforced by db.py)
    return {
        "scan_id": scan_id,
        "scan_time": scan_time,
        "strategy_name": strategy.name,
        "ticker": clean_ticker,
        "current_price": round(latest_close, 2),
        "signal": signal,
        "key_metrics": strategy.format_metrics(df),
        "weekly_change_pct": bar_change,
        # same value as hist_avg_return: avg 4w forward return of this
        # ticker's historical signals (used by DB win-rate aggregation)
        "forward_4w_ret": avg_ret,
        "hist_signal_count": n_signals,
        "hist_avg_return": avg_ret,
        "hist_signal_dates": signal_dates,
        "zone_breakout": bool(zone.get("broke_out", False)),
        "profit_taking_warning": warn,
        "breakout_direction": zone.get("direction"),
        "stop_loss_price": (
            round(zone["stop_loss"], 2) if zone.get("stop_loss") else None
        ),
        # Internal rank key — dropped by storage.db (canonical columns only)
        "sort_value": float(strategy.sort_value(df)),
    }


def _evaluate_ticker(
    fyers,
    ticker: str,
    specs,                      # List[(strategy_obj, scan_id, scan_time)]
    min_history_days: int,
    forward_weeks: int,
) -> List[Tuple[str, Optional[Dict[str, Any]], str]]:
    """Fetch data ONCE per needed resolution -> evaluate EVERY strategy.

    Multi-strategy bench scans share the expensive part (API fetches):
    daily data is fetched at most once per ticker, weekly frames are
    resampled from it, and the 60-min frame is fetched only if some
    selected strategy uses it.

    Returns a list aligned with `specs`, each (scan_id, row, reason):
      - reason "ok"               row has EXACTLY SCAN_COLUMNS
      - reason "fetch_failed"     API/network failure even after retries
      - reason "insufficient_data" valid response but too few bars
      - reason "exception:<msg>"  unexpected error
    """
    import time

    need_daily = any(s.timeframe in ("daily", "weekly") for s, _, _ in specs)
    need_60 = any(s.timeframe == "60min" for s, _, _ in specs)

    def _fetch(resolution: str, days: int):
        """Backoff retries (throttled 4-worker load can trip Fyers).
        None = retryable failure; DataFrame = got a response (may be empty)."""
        for attempt in range(3):
            out = fetch_historical_data(
                fyers, ticker, days=days, resolution=resolution
            )
            if out is not None:
                return out
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))  # 1.5s, 3.0s backoff
        return None

    daily = _fetch("1D", min_history_days) if need_daily else None
    # Fyers hard-caps intraday ranges (resolutions 1..240 min) at 100 days
    # — verified empirically: larger ranges return code -50 every time.
    sixty = _fetch("60", SIXTY_MIN_HISTORY_DAYS) if need_60 else None

    # --- Frames per timeframe (None marks a failed fetch) ----------------
    frames: Dict[str, Optional[pd.DataFrame]] = {}
    if need_daily:
        frames["daily"] = daily
        if daily is None:
            frames["weekly"] = None
        elif daily.empty:
            frames["weekly"] = pd.DataFrame()
        else:
            frames["weekly"] = resample_to_weekly(daily)
    if need_60:
        frames["60min"] = sixty

    # --- Evaluate each strategy on its own frame -------------------------
    out: List[Tuple[str, Optional[Dict[str, Any]], str]] = []
    for strategy, scan_id, scan_time in specs:
        df = frames.get(strategy.timeframe)
        if df is None:
            out.append((scan_id, None, "fetch_failed"))
        elif df.empty or len(df) < strategy.min_bars:
            out.append((scan_id, None, "insufficient_data"))
        else:
            try:
                row = _signal_row(
                    strategy, df, ticker, scan_id, scan_time, forward_weeks
                )
                out.append((scan_id, row, "ok"))
            except Exception as e:
                out.append((scan_id, None, f"exception:{type(e).__name__}: {e}"))
    return out


# ------------------------------------------------------------------
# Universe scan
# ------------------------------------------------------------------

def scan_universe(
    fyers,
    strategy_key: str = "hilega_milega",
    universe: str = "full",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    send_alert: bool = True,
) -> Dict[str, Any]:
    """Run one or more strategies across the NSE universe.

    `strategy_key` may be a comma-separated list (e.g.
    "volcano_watch,volcano_blast") to run a test-bench comparison —
    data is fetched once per ticker and shared by all strategies.
    Returns a single results dict, or {"multi": True, "scans": [...]}
    for a bench run.
    """
    keys = [k.strip() for k in str(strategy_key).split(",") if k.strip()]
    if not keys:
        return {"error": "No strategy selected."}
    resolved = []
    for k in keys:
        entry = get_strategy(k)
        if entry is None:
            return {
                "error": f"Unknown strategy '{k}'. "
                         f"Available: {list(list_strategies().keys())}"
            }
        resolved.append(entry["strategy_obj"])

    cfg = UNIVERSE_SETTINGS
    min_price = cfg["min_price"] if min_price is None else min_price
    max_price = cfg["max_price"] if max_price is None else max_price

    scan_time = datetime.datetime.now()
    stamp = scan_time.strftime("%Y%m%d_%H%M%S")
    specs = [  # (strategy_obj, scan_id, scan_time) — one entry per strategy
        (obj, f"{obj.key.upper()}_{stamp}", scan_time) for obj in resolved
    ]
    multi = len(specs) > 1

    # --- 1. Universe ------------------------------------------------
    if universe == "nifty50":
        raw_tickers = get_nifty50_symbols()
        _log(f"📯 Universe: Nifty 50 sample ({len(raw_tickers)} symbols)")
    else:
        raw_tickers = fetch_nse_universe()

    # --- 2. Price filter ---------------------------------------------
    universe_list = filter_by_price(fyers, raw_tickers, min_price, max_price)
    if not universe_list:
        _log("⚠️  No stocks passed the price filter.")
        universe_list = raw_tickers[:5]  # don't hard-fail an empty scan

    total = len(universe_list)
    label = ", ".join(s[0].name for s in specs) if multi else specs[0][0].name
    _log(
        f"\n🚀 {label} scan: {total} stocks "
        f"(₹{min_price:,.0f}–₹{max_price:,.0f}), "
        f"{cfg['max_workers']} workers"
    )
    _log("-" * 60)

    # --- 3. Evaluate each ticker (all strategies per fetch) -------------
    rows_by: List[List[Dict[str, Any]]] = [[] for _ in specs]
    skips_by: List[Counter] = [Counter() for _ in specs]
    done = 0
    progress_step = max(1, total // 20)

    with ThreadPoolExecutor(max_workers=cfg["max_workers"]) as pool:
        futures = {
            pool.submit(
                _evaluate_ticker,
                fyers,
                tk,
                specs,
                cfg["min_history_days"],
                cfg["forward_weeks"],
            ): tk
            for tk in universe_list
        }

        for fut in as_completed(futures):
            done += 1
            if done % progress_step == 0 or done == total:
                _log(f"⏳ Progress: [{done}/{total}] scanned")

            for i, (_sid, row, reason) in enumerate(fut.result()):
                if row is None:
                    skips_by[i][reason.split(":")[0]] += 1
                    if reason.startswith("exception"):
                        # keep one sample for the report
                        skips_by[i]["_last_error"] = reason
                    continue
                rows_by[i].append(row)
                if row["signal"] == "BUY":
                    tag = f"[{specs[i][0].name}] " if multi else ""
                    _log(
                        f"🟢 {tag}BUY {row['ticker']} @ ₹{row['current_price']} | "
                        f"{row['key_metrics']}"
                    )

    # Skip breakdown — surfaces silent data problems instead of hiding them
    for i, (strat, _sid, _stime) in enumerate(specs):
        skips = skips_by[i]
        total_skipped = sum(v for k, v in skips.items() if not k.startswith("_"))
        if not total_skipped:
            continue
        parts = [
            f"{k.replace('_', ' ')}: {v}"
            for k, v in sorted(skips.items())
            if not k.startswith("_") and v
        ]
        tag = f"[{strat.name}] " if multi else ""
        _log(f"⚠️  {tag}Skipped {total_skipped} of {total} ({'; '.join(parts)})")
        if "_last_error" in skips:
            _log(f"   sample error: {skips['_last_error']}")

    # --- 4. Build one results dict per strategy ---------------------------
    top_n = cfg["top_n_display"]
    all_results: List[Dict[str, Any]] = []
    for i, (strat, sid, stime) in enumerate(specs):
        rows = rows_by[i]
        buys = [r for r in rows if r["signal"] == "BUY"]
        buys.sort(key=lambda r: r.get("sort_value", 0.0), reverse=True)
        all_results.append({
            "scan_id": sid,
            "scan_time": stime,
            "strategy": strat.name,
            "strategy_key": strat.key,
            "timeframe": strat.timeframe,
            "universe": universe,
            "universe_size": len(raw_tickers),
            "filtered_size": len(universe_list),
            "scanned": len(rows),
            "skipped": {
                k: v for k, v in skips_by[i].items() if not k.startswith("_")
            },
            "buy_count": len(buys),
            "hold_count": sum(1 for r in rows if r["signal"] == "HOLD"),
            "sell_count": sum(1 for r in rows if r["signal"] == "SELL"),
            "buy_signals": buys,
            "top_buys": buys[:top_n],
            "all_results": rows,
        })

    # --- 5. Persist + alert --------------------------------------------
    try:
        init_db()
        for res in all_results:
            save_scan_results(res, res["strategy"])
    except Exception as e:
        _log(f"⚠️  DB save skipped: {e}")

    if send_alert:
        try:
            _send_telegram(all_results if multi else all_results[0], top_n)
        except Exception as e:
            _log(f"⚠️  Telegram skipped: {e}")

    for res in all_results:
        _print_summary(res)

    if not multi:
        return all_results[0]

    return {
        "multi": True,
        "scans": all_results,
        "scan_time": scan_time,
        "scan_id": all_results[0]["scan_id"],
        "strategy_names": [r["strategy"] for r in all_results],
        # Aggregates for the dashboard headline row (per-tab detail in scans)
        "scanned": max(r["scanned"] for r in all_results),
        "buy_count": sum(r["buy_count"] for r in all_results),
        "sell_count": sum(r["sell_count"] for r in all_results),
        "hold_count": sum(r["hold_count"] for r in all_results),
    }


def _print_summary(results: Dict[str, Any]) -> None:
    top_buys = results["top_buys"]
    _log("\n" + "=" * 60)
    _log(f"📊 {results['strategy'].upper()} — SCAN RESULTS")
    _log("=" * 60)
    _log(f"   Scan ID     : {results['scan_id']}")
    _log(f"   Universe    : {results['universe_size']} stocks "
         f"-> {results['filtered_size']} after price filter")
    _log(f"   Scanned     : {results['scanned']}")
    _log(f"   BUY / SELL / HOLD : "
         f"{results['buy_count']} / {results['sell_count']} / {results['hold_count']}")

    if top_buys:
        _log(f"\n🚀 TOP {len(top_buys)} BUY SETUPS:")
        _log("-" * 60)
        for i, r in enumerate(top_buys, 1):
            flags = []
            if r["zone_breakout"]:
                flags.append(f"⚠️ zone break ({r['breakout_direction']}, stop ₹{r['stop_loss_price']})")
            if r["profit_taking_warning"]:
                flags.append("💡 profit-taking warning (EMA≈RSI)")
            extra = (" | " + " ".join(flags)) if flags else ""
            _log(f"{i:>2}. {r['ticker']:<14} ₹{r['current_price']:>9,.2f}  "
                 f"chg {r['weekly_change_pct']:+.2f}%  "
                 f"hist {r['hist_signal_count']} sig / {r['hist_avg_return']:+.2f}%{extra}")
            _log(f"    {r['key_metrics']}")
    else:
        _log("\n⚪ No BUY signals this scan.")
    _log("=" * 60 + "\n")


def _send_telegram(results, top_n: int) -> None:
    """Send one alert. Accepts a single results dict or a list of them
    (multi-strategy bench runs send one combined digest)."""
    if isinstance(results, dict):
        results = [results]

    if len(results) == 1:
        r = results[0]
        msg = f"📊 *{r['strategy'].upper()} SCAN*\n"
        msg += f"🗓 `{r['scan_time'].strftime('%Y-%m-%d %H:%M')}`\n"
        msg += f"Scanned {r['scanned']} | BUY: *{r['buy_count']}*\n"
        msg += "──────────────────────\n"

        if r["top_buys"]:
            msg += f"🚀 *TOP {len(r['top_buys'])} BUY SETUPS:*\n\n"
            for s in r["top_buys"]:
                msg += (f"• *{s['ticker']}* ₹{s['current_price']} "
                        f"(`{s['weekly_change_pct']:+.2f}%`)\n"
                        f"  {s['key_metrics']}\n"
                        f"  hist: {s['hist_signal_count']} sig | "
                        f"{s['hist_avg_return']:+.2f}%\n\n")
        else:
            msg += "⚪ *No BUY signals detected.*\n"

        send_telegram_alert(msg)
        return

    # --- Multi-strategy digest: one section per strategy -----------------
    total_buys = sum(r["buy_count"] for r in results)
    msg = "📊 *MULTI-STRATEGY SCAN*\n"
    msg += f"🗓 `{results[0]['scan_time'].strftime('%Y-%m-%d %H:%M')}`\n"
    msg += (f"Scanned {max(r['scanned'] for r in results)} | "
            f"BUY total: *{total_buys}*\n")
    msg += "──────────────────────\n"
    for r in results:
        msg += f"\n🔥 *{r['strategy']}* — BUY: {r['buy_count']}\n"
        if not r["top_buys"]:
            msg += "⚪ none\n"
            continue
        for s in r["top_buys"][:5]:  # keep the digest readable
            msg += (f"• *{s['ticker']}* ₹{s['current_price']} "
                    f"(`{s['weekly_change_pct']:+.2f}%`)\n"
                    f"  {s['key_metrics']}\n")

    send_telegram_alert(msg)


# ------------------------------------------------------------------
# Public entry point (used by app.py and the CLI)
# ------------------------------------------------------------------

def run_screener(
    strategy_key: str = "hilega_milega",
    universe: str = "full",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    send_alert: bool = True,
) -> Dict[str, Any]:
    """Full pipeline: auth check -> universe -> scan -> DB -> Telegram."""
    _log("\n" + "=" * 60)
    _log("🧮 NSE QUANT SCREENER")
    _log("=" * 60)

    fyers = get_fyers_client()
    if fyers is None:
        return {"error": "auth_required",
                "message": "Run 'python core/auth.py' to log in (Option A)."}

    return scan_universe(
        fyers,
        strategy_key=strategy_key,
        universe=universe,
        min_price=min_price,
        max_price=max_price,
        send_alert=send_alert,
    )


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def _interactive_menu() -> Optional[str]:
    strategies = list_strategies()
    keys = list(strategies.keys())
    if not keys:
        _log("❌ No strategies registered. See strategies/registry.py")
        return None

    _log("\n" + "=" * 50)
    _log("📈 SCREENER MENU")
    _log("=" * 50)
    for i, k in enumerate(keys, 1):
        _log(f"{i}. {strategies[k]}")
    _log(f"{len(keys) + 1}. Exit")

    choice = input(f"\nSelect (1-{len(keys) + 1}): ").strip()
    if choice == str(len(keys) + 1) or choice.lower() in ("q", "exit"):
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(keys):
        return keys[int(choice) - 1]
    if choice in keys:  # allow typing the key directly
        return choice
    _log("❌ Invalid selection.")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NSE Stock Screener (Hilega Milega & more)"
    )
    parser.add_argument(
        "-s", "--strategy",
        default=None,
        help="Strategy key(s), comma-separated for a bench run "
             f"(available: {', '.join(list_strategies().keys())})",
    )
    parser.add_argument(
        "-u", "--universe",
        choices=["full", "nifty50"],
        default="full",
        help="'full' NSE list or 'nifty50' sample (default: full)",
    )
    parser.add_argument("--min-price", type=float, default=None)
    parser.add_argument("--max-price", type=float, default=None)
    parser.add_argument(
        "--no-alert", action="store_true", help="Skip Telegram notification"
    )
    args = parser.parse_args()

    strategy_key = args.strategy
    if strategy_key is None:
        strategy_key = _interactive_menu()
        if strategy_key is None:
            _log("Exiting. Goodbye!")
            return

    run_screener(
        strategy_key=strategy_key,
        universe=args.universe,
        min_price=args.min_price,
        max_price=args.max_price,
        send_alert=not args.no_alert,
    )


if __name__ == "__main__":
    main()
