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

import pandas as pd

# Project root on sys.path so `core`, `strategies`, `storage`, `config` resolve
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

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

# Serialize console output from worker threads
_print_lock = threading.Lock()


def _log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


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

def _evaluate_ticker(
    fyers,
    ticker: str,
    strategy,
    min_bars: int,
    min_history_days: int,
    forward_weeks: int,
    scan_id: str,
    scan_time: datetime.datetime,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Fetch -> weekly resample -> indicators -> signal -> backtest stats.

    Returns (row, reason):
      - (row_dict, "ok") on success — row has EXACTLY storage.db.SCAN_COLUMNS
        plus an internal "sort_value" (dropped by db.py)
      - (None, reason) to skip, where reason is one of:
        "fetch_failed"      API/network failure even after retries
        "insufficient_data" valid response but too few bars
        "exception:<msg>"   unexpected error
    """
    import time

    # --- Fetch with backoff retries (throttled 4-worker load can trip Fyers)
    max_attempts = 3
    daily = None
    for attempt in range(max_attempts):
        daily = fetch_historical_data(
            fyers, ticker, days=min_history_days, resolution="1D"
        )
        if daily is not None:
            break
        if attempt < max_attempts - 1:
            time.sleep(1.5 * (attempt + 1))  # 1.5s, 3.0s backoff

    if daily is None:
        return None, "fetch_failed"
    if daily.empty:
        return None, "insufficient_data"

    try:
        weekly = resample_to_weekly(daily)
        if weekly.empty or len(weekly) < min_bars:
            return None, "insufficient_data"

        # --- Indicators ------------------------------------------------
        weekly = strategy.compute(weekly)
        buy_series = strategy.buy_signal(weekly)
        sell_series = strategy.sell_signal(weekly)

        is_buy = bool(buy_series.iloc[-1])
        is_sell = bool(sell_series.iloc[-1])
        signal = "BUY" if is_buy else ("SELL" if is_sell else "HOLD")

        # --- Prices / weekly change -------------------------------------
        latest_close = float(weekly["Close"].iloc[-1])
        prev_close = float(weekly["Close"].iloc[-2])
        weekly_change = round((latest_close - prev_close) / prev_close * 100, 2)

        # --- Historical backtest (exclude the live bar) ------------------
        hist_mask = buy_series.iloc[:-1]
        fwd_ret = (
            (weekly["Close"].shift(-forward_weeks) - weekly["Close"])
            / weekly["Close"]
            * 100
        )
        eligible = hist_mask & fwd_ret.notna()
        n_signals = int(eligible.sum())
        avg_ret = round(float(fwd_ret[eligible].mean()), 2) if n_signals else 0.0

        if n_signals:
            if "Timestamp" in weekly.columns:
                dates = pd.to_datetime(weekly.loc[eligible, "Timestamp"])
            else:  # fallback: datetime index
                dates = pd.to_datetime(weekly.index[eligible.values])
            date_list = dates.dt.strftime("%Y-%m-%d").tolist()
            signal_dates = ", ".join(date_list[-10:])
            if len(date_list) > 10:
                signal_dates = f"...{signal_dates}"
        else:
            signal_dates = ""

        # --- Zone breakout / profit warning (optional strategy hooks) ----
        zone = {}
        zone_fn = getattr(strategy, "detect_zone_breakout", None)
        if callable(zone_fn):
            zone = zone_fn(weekly) or {}

        warn = False
        warn_fn = getattr(strategy, "profit_taking_warning", None)
        if callable(warn_fn):
            warn = bool(warn_fn(weekly))

        clean_ticker = ticker.replace("NSE:", "").replace("-EQ", "")

        # Row keys inserted in SCAN_COLUMNS order (also enforced by db.py)
        return {
            "scan_id": scan_id,
            "scan_time": scan_time,
            "strategy_name": strategy.name,
            "ticker": clean_ticker,
            "current_price": round(latest_close, 2),
            "signal": signal,
            "key_metrics": strategy.format_metrics(weekly),
            "weekly_change_pct": weekly_change,
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
            "sort_value": float(strategy.sort_value(weekly)),
        }, "ok"

    except Exception as e:
        return None, f"exception:{type(e).__name__}: {e}"


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
    """Run one strategy across the NSE universe. Returns a results dict."""
    strategy = get_strategy(strategy_key)
    if strategy is None:
        return {
            "error": f"Unknown strategy '{strategy_key}'. "
                     f"Available: {list(list_strategies().keys())}"
        }

    cfg = UNIVERSE_SETTINGS
    strategy_obj = strategy["strategy_obj"]
    min_price = cfg["min_price"] if min_price is None else min_price
    max_price = cfg["max_price"] if max_price is None else max_price

    scan_time = datetime.datetime.now()
    scan_id = f"{strategy_obj.key.upper()}_{scan_time.strftime('%Y%m%d_%H%M%S')}"

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
    _log(
        f"\n🚀 {strategy_obj.name} scan: {total} stocks "
        f"(₹{min_price:,.0f}–₹{max_price:,.0f}), "
        f"{cfg['max_workers']} workers"
    )
    _log("-" * 60)

    # --- 3. Evaluate each ticker --------------------------------------
    rows: List[Dict[str, Any]] = []
    skips: Counter = Counter()
    done = 0
    progress_step = max(1, total // 20)

    with ThreadPoolExecutor(max_workers=cfg["max_workers"]) as pool:
        futures = {
            pool.submit(
                _evaluate_ticker,
                fyers,
                tk,
                strategy_obj,
                cfg["min_weekly_bars"],
                cfg["min_history_days"],
                cfg["forward_weeks"],
                scan_id,
                scan_time,
            ): tk
            for tk in universe_list
        }

        for fut in as_completed(futures):
            done += 1
            if done % progress_step == 0 or done == total:
                _log(f"⏳ Progress: [{done}/{total}] scanned")

            row, reason = fut.result()
            if row is None:
                skips[reason.split(":")[0]] += 1
                if reason.startswith("exception"):
                    skips["_last_error"] = reason  # keep one sample for report
                continue
            rows.append(row)
            if row["signal"] == "BUY":
                _log(
                    f"🟢 BUY {row['ticker']} @ ₹{row['current_price']} | "
                    f"{row['key_metrics']}"
                )

    # Skip breakdown — surfaces silent data problems instead of hiding them
    if skips:
        parts = [
            f"{k.replace('_', ' ')}: {v}"
            for k, v in sorted(skips.items())
            if not k.startswith("_") and v
        ]
        _log(f"⚠️  Skipped {sum(v for k, v in skips.items() if not k.startswith('_'))} "
             f"of {total} ({'; '.join(parts)})")
        if "_last_error" in skips:
            _log(f"   sample error: {skips['_last_error']}")

    # --- 4. Rank BUY signals -------------------------------------------
    buys = [r for r in rows if r["signal"] == "BUY"]
    buys.sort(key=lambda r: r.get("sort_value", 0.0), reverse=True)
    top_n = cfg["top_n_display"]
    top_buys = buys[:top_n]

    results = {
        "scan_id": scan_id,
        "scan_time": scan_time,
        "strategy": strategy_obj.name,
        "strategy_key": strategy_obj.key,
        "universe": universe,
        "universe_size": len(raw_tickers),
        "filtered_size": len(universe_list),
        "scanned": len(rows),
        "skipped": {k: v for k, v in skips.items() if not k.startswith("_")},
        "buy_count": len(buys),
        "hold_count": sum(1 for r in rows if r["signal"] == "HOLD"),
        "sell_count": sum(1 for r in rows if r["signal"] == "SELL"),
        "buy_signals": buys,
        "top_buys": top_buys,
        "all_results": rows,
    }

    # --- 5. Persist + alert --------------------------------------------
    try:
        init_db()
        save_scan_results(results, strategy_obj.name)
    except Exception as e:
        _log(f"⚠️  DB save skipped: {e}")

    if send_alert:
        try:
            _send_telegram(results, top_n)
        except Exception as e:
            _log(f"⚠️  Telegram skipped: {e}")

    _print_summary(results)
    return results


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


def _send_telegram(results: Dict[str, Any], top_n: int) -> None:
    msg = f"📊 *{results['strategy'].upper()} SCAN*\n"
    msg += f"🗓 `{results['scan_time'].strftime('%Y-%m-%d %H:%M')}`\n"
    msg += f"Scanned {results['scanned']} | BUY: *{results['buy_count']}*\n"
    msg += "──────────────────────\n"

    if results["top_buys"]:
        msg += f"🚀 *TOP {len(results['top_buys'])} BUY SETUPS:*\n\n"
        for s in results["top_buys"]:
            msg += (f"• *{s['ticker']}* ₹{s['current_price']} "
                    f"(`{s['weekly_change_pct']:+.2f}%`)\n"
                    f"  {s['key_metrics']}\n"
                    f"  hist: {s['hist_signal_count']} sig | "
                    f"{s['hist_avg_return']:+.2f}%\n\n")
    else:
        msg += "⚪ *No BUY signals detected.*\n"

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
        help=f"Strategy key: {', '.join(list_strategies().keys())}",
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
