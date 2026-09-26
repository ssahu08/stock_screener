# Replay tool — run any strategy over one symbol's history
# ==========================================================
# Fyers first (your broker's feed = what the app really uses),
# automatic yfinance fallback when no valid token exists.
#
#   python tools/replay_bb_pivot.py DRREDDY
#   python tools/replay_bb_pivot.py DRREDDY --source yfinance --days 730
#   python tools/replay_bb_pivot.py NIFTY50
#   python tools/replay_bb_pivot.py DRREDDY --strategy bb_pivot_washout_60
#   python tools/replay_bb_pivot.py DRREDDY --strategy volcano_blast
#
# Fyers caps 1D ranges at 366 days per request, so long windows are
# fetched in chunks of <=360 days and stitched together (intraday caps
# are 100 days -> <=95-day chunks).

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

FYERS_CAP_DAYS = {"1D": 360, "60": 95, "60min": 95}
FYERS_RESOLUTION = {"daily": "1D", "weekly": "1D", "60min": "60"}
YF_INTERVAL = {"daily": "1d", "weekly": "1d", "60min": "1h"}
# forward-return horizon in BARS — matches engine forward_weeks(4) x bars_per_week
FWD_BARS = {"weekly": (4, 16), "daily": (5, 20), "60min": (32, 128)}
YF_PERIODS = [(365, "1y"), (730, "2y"), (1825, "5y"), (10 ** 9, "10y")]
INDEX_MAP = {"NIFTY50": "^NSEI", "NIFTYBANK": "^NSEBANK"}


def normalize(symbol: str):
    """-> (fyers_symbol, yfinance_symbol, display_label)"""
    s = str(symbol).strip().upper()
    if s in INDEX_MAP:                              # NIFTY50
        return f"NSE:{s}-INDEX", INDEX_MAP[s], s
    if s.endswith(".NS"):                           # DRREDDY.NS
        base = s[:-3]
        return f"NSE:{base}-EQ", s, base
    if ":" in s:                                    # NSE:DRREDDY-EQ | NSE:NIFTY50-INDEX
        name = s.split(":", 1)[1]
        if name.endswith("-INDEX"):
            base = name[: -len("-INDEX")]
            return s, INDEX_MAP.get(base, base), base
        base = name[:-3] if name.endswith("-EQ") else name
        return s, base + ".NS", base
    return f"NSE:{s}-EQ", s + ".NS", s              # plain ticker: DRREDDY


def fetch_fyers(fyers, fsym: str, resolution: str, days: int):
    """Chunked Fyers fetch (per-request range caps) -> DataFrame | None."""
    import core.data as cdata  # local import: keeps this tool's startup light

    cap = FYERS_CAP_DAYS.get(resolution, 360)
    end = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    frames, cursor_end, failures = [], end, 0

    while cursor_end > start:
        cursor_start = max(start, cursor_end - datetime.timedelta(days=cap))
        payload = {
            "symbol": fsym,
            "resolution": resolution,
            "date_format": "1",
            "range_from": cursor_start.strftime("%Y-%m-%d"),
            "range_to": cursor_end.strftime("%Y-%m-%d"),
        }
        try:
            time.sleep(0.15)
            resp = fyers.history(data=payload)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! Fyers request failed ({cursor_start}..{cursor_end}): {exc}")
            resp = None
        if isinstance(resp, dict) and resp.get("s") == "ok":
            candles = resp.get("candles") or []
            if candles:
                df = pd.DataFrame(
                    candles,
                    columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"],
                )
                df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit="s")
                frames.append(df.set_index("Timestamp"))
        else:
            failures += 1
            print(f"  ! Fyers chunk {cursor_start}..{cursor_end} rejected"
                  + (f": {resp.get('message')}" if isinstance(resp, dict) else ""))
        cursor_end = cursor_start

    if failures and not frames:
        return None
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames).sort_index()
    return df[~df.index.duplicated(keep="last")]


def fetch_yfinance(yfsym: str, resolution: str, days: int):
    import yfinance as yf

    period = next(p for limit, p in YF_PERIODS if days <= limit)
    raw = yf.download(
        yfsym, period=period, interval=YF_INTERVAL[resolution],
        auto_adjust=False, progress=False,
    )
    if raw is None or raw.empty:
        return None
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = raw [["Open", "High", "Low", "Close", "Volume"]].dropna()
    df = df[~df.index.duplicated(keep="last")]
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay a strategy on one symbol.")
    ap.add_argument("symbol", help="DRREDDY | DRREDDY.NS | NSE:DRREDDY-EQ | NIFTY50")
    ap.add_argument("--strategy", default="bb_pivot_washout",
                    help="registry key (default: bb_pivot_washout)")
    ap.add_argument("--source", choices=["auto", "fyers", "yfinance"], default="auto")
    ap.add_argument("--days", type=int, default=730, help="history window (default 730)")
    args = ap.parse_args()

    from strategies.registry import get_strategy

    entry = get_strategy(args.strategy)
    if entry is None:
        print(f"Unknown strategy '{args.strategy}'. "
              f"Available: {sorted(__import__('strategies.registry', fromlist=['x']).STRATEGY_REGISTRY)}")
        return 2
    strat = entry["strategy_obj"]
    tf = strat.timeframe
    fsym, yfsym, label = normalize(args.symbol)
    resolution = FYERS_RESOLUTION[tf]

    # --- source selection -------------------------------------------------
    df, source = None, None
    if args.source in ("auto", "fyers"):
        from engine import get_fyers_client

        fyers = get_fyers_client()
        if fyers is not None:
            print(f"[1/2] Fetching {fsym} from Fyers ({resolution}, {args.days}d, chunked)…")
            df = fetch_fyers(fyers, fsym, resolution, args.days)
            source = "Fyers API"
            if (df is None or df.empty) and args.source == "fyers":
                print("Fyers fetch failed — nothing to replay.")
                return 2
        elif args.source == "fyers":
            print("No valid Fyers token — run 'python core/auth.py' or drop --source fyers.")
            return 2
        else:
            print("[1/2] No valid Fyers token -> falling back to yfinance.")
    if df is None or df.empty:
        print(f"[1/2] Fetching {yfsym} from yfinance ({YF_INTERVAL[tf]}, {args.days}d)…")
        df = fetch_yfinance(yfsym, resolution, args.days)
        source = "yfinance (unofficial — validate on Fyers before trusting dates)"
    if df is None or df.empty:
        print("No data from either source.")
        return 2

    if tf == "weekly":
        from core.data import resample_to_weekly

        df = resample_to_weekly(df)

    # --- replay ------------------------------------------------------------
    print(f"[2/2] Replaying {entry['name']} on {len(df)} {tf} bars…\n")
    out = strat.compute(df)
    buy = strat.buy_signal(out)
    f_short, f_long = FWD_BARS.get(tf, (5, 20))
    fwd_s = (out["Close"].shift(-f_short) - out["Close"]) / out["Close"] * 100
    fwd_l = (out["Close"].shift(-f_long) - out["Close"]) / out["Close"] * 100

    print(f"{label} — {entry['name']} ({args.strategy})")
    print(f"Source : {source}")
    print(f"Window : {out.index[0].date()} .. {out.index[-1].date()} | {len(out)} {tf} bars")
    sigs = buy[buy]
    hist = sigs.iloc[:-1] if len(sigs) and sigs.index[-1] == out.index[-1] else sigs
    print(f"Signals: {len(sigs)} total | {len(hist)} historical (live bar excluded from stats)")

    if len(hist):
        print(f"\n  {'date':<11} {'close':>9} {'shelf':>5} {'gap%':>6} "
              f"{'O-dist':>7} {'C-band':>7} {f'+{f_short}d':>8} {f'+{f_long}d':>8}")
        for ts in hist.index:
            r = out.loc[ts]
            print(f"  {ts.date()!s:<11} {r['Close']:>9.2f} {str(r['PIV_NAME']):>5} "
                  f"{r['GAP_PCT']:>6.2f} {r['OPEN_DIST_PCT']:>6.2f}% "
                  f"{r['PEN_PCT']:>+6.2f}% {fwd_s.loc[ts]:>+7.2f}% {fwd_l.loc[ts]:>+7.2f}%")
        hs, hl = fwd_s.loc[hist.index].dropna(), fwd_l.loc[hist.index].dropna()
        if len(hs):
            print(f"\n  avg +{f_short}d {hs.mean():+.2f}% ({(hs > 0).mean() * 100:.0f}% > 0) | "
                  f"avg +{f_long}d {hl.mean():+.2f}% ({(hl > 0).mean() * 100:.0f}% > 0)  "
                  f"[historical n={len(hs)} — small sample]")
    else:
        print("\n  No signals in this window.")

    print(f"\nCURRENT STATE — last bar {out.index[-1].date()}:")
    print(f"  {strat.format_metrics(out)}")
    if bool(buy.iloc[-1]):
        print("  >>> LIVE SIGNAL on the last bar <<<")
    return 0


if __name__ == "__main__":
    sys.exit(main())
