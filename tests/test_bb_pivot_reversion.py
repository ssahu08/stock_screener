# Test bench — strategies/bb_pivot_reversion.py (feature/bb-pivot-test)
# ======================================================================
# Run:  python tests/test_bb_pivot_reversion.py
#
# Frames are hand-crafted so every gate of the setup can be isolated:
#   1. Idea 1  — |BB_lo - nearest pivot line| <= 0.5%  (confluence)
#   2. Idea 2a — |Open - BB_lo| <= 0.5%                (opens near band)
#   3. Idea 2b — Close < BB_LO                         (washout close)
# The previous bar's High/Low are placed AFTER the closes are fixed
# (BB depends on closes only), so pivot lines land exactly where each
# case needs them.

import datetime
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import numpy as np
import pandas as pd

from strategies.bb_pivot_reversion import (
    BBPivotWashout60,
    BBPivotWashoutDaily,
    PIVOT_NAMES,
    _pivot_lines,
)

FAILURES = []


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def make_frame(closes, opens=None, highs=None, lows=None, index=None):
    closes = [float(c) for c in closes]
    n = len(closes)
    if index is None:
        index = pd.date_range("2026-03-02", periods=n, freq="B")
    opens = [float(o) for o in (opens if opens is not None else closes)]
    highs = [float(h) for h in (
        highs if highs is not None else [c * 1.01 for c in closes]
    )]
    lows = [float(lo) for lo in (
        lows if lows is not None else [c * 0.99 for c in closes]
    )]
    return pd.DataFrame(
        {
            "Open": opens,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": [100_000] * n,
        },
        index=index,
    )


def bb_lo(closes, n=20, k=2.0):
    """Independent expectation of BB(n, k, Close, simple, population std)."""
    w = np.asarray(closes[-n:], dtype=float)
    return float(w.mean() - k * w.std(ddof=0))


def check_ohlc(df):
    hi_ok = df["High"] >= df[["Open", "Close"]].max(axis=1) - 1e-9
    lo_ok = df["Low"] <= df[["Open", "Close"]].min(axis=1) + 1e-9
    rng_ok = df["High"] >= df["Low"] - 1e-9
    bad = ~(hi_ok & lo_ok & rng_ok)
    assert not bool(bad.any()), f"invalid OHLC at {list(df.index[bad])}"


def set_col(df, i, col, val):
    df.iloc[i, df.columns.get_loc(col)] = float(val)


def place_prev_p(df, level, spread=None):
    """Set the previous bar's High/Low so standard pivots give P == level.

    P = (H+L+C)/3 = level  =>  H+L = 3*level - C; we place H/L symmetric
    around that centre, `spread` apart (default 10% of price so the other
    six lines sit far away and cannot satisfy the 0.5% gate by accident).
    """
    df = df.copy()
    c = float(df["Close"].iloc[-2])
    if spread is None:
        spread = 0.10 * c
    centre = 1.5 * level - 0.5 * c
    h, lo = centre + spread / 2.0, centre - spread / 2.0
    assert lo > 0, f"non-positive low {lo}"
    assert h >= c - 1e-9 and lo <= c + 1e-9, (
        f"pivot bar invalid: H={h:.3f} L={lo:.3f} C={c:.3f}"
    )
    set_col(df, -2, "High", h)
    set_col(df, -2, "Low", lo)
    return df


def base_flat():
    """30 closes: 29x100 then a 97 washout candidate; open high fixed later."""
    return [100.0] * 29 + [97.0]


def trigger(df, open_, high, low):
    i = len(df) - 1
    set_col(df, i, "Open", open_)
    set_col(df, i, "High", high)
    set_col(df, i, "Low", low)
    return df


def firing_frame():
    """The canonical setup: P sits on BB_lo, candle opens near, closes below."""
    closes = base_flat()
    b = bb_lo(closes)
    df = make_frame(closes)
    df = place_prev_p(df, b)
    df = trigger(df, open_=b + 0.2, high=b + 1.5, low=96.5)
    check_ohlc(df)
    return df, b


def run(df):
    strat = BBPivotWashoutDaily()
    out = strat.compute(df)
    return strat, out, strat.buy_signal(out)


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

def test_pivot_formulas():
    closes = [95.0] + [100.0] * 24
    df = make_frame(closes)
    df = df.copy()
    set_col(df, 0, "High", 100.0)
    set_col(df, 0, "Low", 90.0)
    check_ohlc(df)
    lines = _pivot_lines(df, "prev_bar")
    expected = {  # textbook: H=100 L=90 C=95
        "P": 95.0, "R1": 100.0, "R2": 105.0, "R3": 110.0,
        "S1": 90.0, "S2": 85.0, "S3": 80.0,
    }
    row = 1  # pivots at bar i come from bar i-1
    for name, series in zip(PIVOT_NAMES, lines):
        got = float(series.iloc[row])
        assert abs(got - expected[name]) < 1e-9, (
            f"{name}: expected {expected[name]}, got {got}"
        )
    assert np.isnan(float(lines[0].iloc[0])), "first bar must have no pivots"


def test_setup_fires():
    df, b = firing_frame()
    strat, out, buy = run(df)
    assert bool(buy.iloc[-1]), "canonical setup must fire"
    assert out["PIV_NAME"].iloc[-1] == "P"
    assert float(out["GAP_PCT"].iloc[-1]) < 1e-6, "P should sit ON the band"
    assert float(out["OPEN_DIST_PCT"].iloc[-1]) <= 0.5
    assert float(out["PEN_PCT"].iloc[-1]) < 0, "close must be below the band"
    # exactly one signal: the trigger bar (flat closes never close below BB)
    assert int(buy.sum()) == 1, f"expected 1 signal, got {int(buy.sum())}"


def test_nearest_line_is_s1():
    """Confluence must pick the CLOSEST of the 7 lines, not always P."""
    closes = [102.0, 106.0] * 14 + [97.0]
    b = bb_lo(closes)
    c_prev = closes[-2]           # 106
    spread = 14.0
    level = 2 * b + spread - c_prev   # => S1 lands exactly on BB_lo
    df = make_frame(closes)
    df = place_prev_p(df, level, spread=spread)
    df = trigger(df, open_=b + 0.2, high=b + 1.5, low=96.8)
    check_ohlc(df)
    strat, out, buy = run(df)
    assert out["PIV_NAME"].iloc[-1] == "S1", (
        f"expected S1, got {out['PIV_NAME'].iloc[-1]} "
        f"(gap {float(out['GAP_PCT'].iloc[-1]):.3f}%)"
    )
    assert float(out["GAP_PCT"].iloc[-1]) < 1e-6
    assert bool(buy.iloc[-1]), "setup must fire via the S1 shelf"
    # P itself is far — proof that nearest-line selection really happened
    p_val = float(_pivot_lines(df, "prev_bar")[0].iloc[-1])
    assert abs(p_val - b) / b * 100 > 0.5


def test_wide_gap_rejected():
    closes = base_flat()
    b = bb_lo(closes)
    df = make_frame(closes)
    df = place_prev_p(df, b * 1.02)      # nearest line ~2% away
    df = trigger(df, open_=b + 0.2, high=b + 1.5, low=96.5)
    check_ohlc(df)
    _, out, buy = run(df)
    gap = float(out["GAP_PCT"].iloc[-1])
    assert 1.8 < gap < 2.2, f"expected ~2% gap, got {gap}"
    assert out["PIV_NAME"].iloc[-1] == "P"
    assert not bool(buy.iloc[-1]), "wide gap must reject the setup"
    assert float(out["OPEN_DIST_PCT"].iloc[-1]) <= 0.5, "open-near gate alone ok"
    assert float(out["PEN_PCT"].iloc[-1]) < 0, "washout gate alone ok"


def test_threshold_boundaries():
    """0.49% fires, 0.60% does not — both above and below the band."""
    closes = base_flat()
    b = bb_lo(closes)

    def attempt(level):
        df = make_frame(closes)
        df = place_prev_p(df, level)
        df = trigger(df, open_=b + 0.2, high=b + 1.5, low=96.5)
        _, out, buy = run(df)
        return float(out["GAP_PCT"].iloc[-1]), bool(buy.iloc[-1])

    gap, fires = attempt(b * 1.0049)
    assert gap <= 0.5 and fires, f"0.49% above: gap {gap} fires {fires}"
    gap, fires = attempt(b * 1.006)
    assert gap > 0.5 and not fires, f"0.60% above: gap {gap} fires {fires}"
    gap, fires = attempt(b / 1.0049)
    assert gap <= 0.5 and fires, f"0.49% below: gap {gap} fires {fires}"


def test_open_far_rejected():
    closes = base_flat()
    b = bb_lo(closes)
    df = make_frame(closes)
    df = place_prev_p(df, b)
    df = trigger(df, open_=b + 3.0, high=b + 4.0, low=96.5)  # opens 3% off band
    check_ohlc(df)
    _, out, buy = run(df)
    assert float(out["GAP_PCT"].iloc[-1]) <= 0.5, "confluence alone ok"
    assert float(out["OPEN_DIST_PCT"].iloc[-1]) > 0.5
    assert not bool(buy.iloc[-1]), "far open must reject the setup"


def test_close_above_band_rejected():
    closes = [100.0] * 29 + [101.0]      # green candle, closes ABOVE band
    b = bb_lo(closes)
    df = make_frame(closes)
    df = place_prev_p(df, b)
    df = trigger(df, open_=b + 0.1, high=102.0, low=99.0)
    check_ohlc(df)
    _, out, buy = run(df)
    assert float(out["GAP_PCT"].iloc[-1]) <= 0.5, "confluence alone ok"
    assert float(out["OPEN_DIST_PCT"].iloc[-1]) <= 0.5, "open-near alone ok"
    assert float(out["Close"].iloc[-1]) > float(out["BB_LO"].iloc[-1])
    assert not bool(buy.iloc[-1]), "close ABOVE band must reject (washout rule)"


def test_equal_close_strict_boundary():
    """Close == BB_lo is NOT a washout (strict '<' as confirmed)."""
    closes = [100.0] * 30
    b = bb_lo(closes)
    assert b == 100.0, "flat closes => band collapses to the price"
    df = make_frame(closes)
    df = place_prev_p(df, b)
    df = trigger(df, open_=100.0, high=101.0, low=99.0)
    check_ohlc(df)
    _, out, buy = run(df)
    assert float(out["GAP_PCT"].iloc[-1]) < 1e-6, "confluence ok"
    assert float(out["OPEN_DIST_PCT"].iloc[-1]) < 1e-6, "open near ok"
    assert not bool(buy.iloc[-1]), "C == BB_lo must NOT fire (strict below)"


def test_small_dip_fires():
    closes = [100.0] * 29 + [99.9]
    b = bb_lo(closes)
    df = make_frame(closes)
    df = place_prev_p(df, b)
    df = trigger(df, open_=100.0, high=101.0, low=99.0)
    check_ohlc(df)
    _, _, buy = run(df)
    assert bool(buy.iloc[-1]), "a marginal washout close must still fire"


def test_no_future_leakage():
    df, _ = firing_frame()
    strat = BBPivotWashoutDaily()
    sig1 = strat.buy_signal(strat.compute(df)).tolist()
    wild = df.copy()
    wild = pd.concat([wild, df.tail(1).assign(
        Open=999.0, High=999.0, Low=999.0, Close=999.0
    )])
    sig2 = strat.buy_signal(strat.compute(wild)).tolist()
    assert sig2[: len(sig1)] == sig1, "future bars changed past signals!"


def test_sell_never_fires():
    df_fire, _ = firing_frame()
    strat = BBPivotWashoutDaily()
    for df in (df_fire, df_fire.head(15)):
        assert not bool(strat.sell_signal(strat.compute(df)).any()), \
            "long-only: SELL must never fire"


def test_metrics_and_ranking():
    df_a, b = firing_frame()
    # looser (but still firing) confluence for ranking comparison
    closes = base_flat()
    df_b = make_frame(closes)
    df_b = place_prev_p(df_b, b * 1.0049)
    df_b = trigger(df_b, open_=b + 0.2, high=b + 1.5, low=96.5)

    strat = BBPivotWashoutDaily()
    out_a = strat.compute(df_a)
    out_b = strat.compute(df_b)
    assert bool(strat.buy_signal(out_a).iloc[-1])
    assert bool(strat.buy_signal(out_b).iloc[-1])
    assert strat.sort_value(out_a) > strat.sort_value(out_b), \
        "tighter confluence must rank higher"

    text = strat.format_metrics(out_a)
    for needle in ("SETUP", "gap", "stop", "T1 mid", "\u20b9"):
        assert needle in text, f"metrics missing '{needle}': {text}"

    warm = strat.compute(df_a.head(15))   # < 20 closes => no BB yet
    assert "warm-up" in strat.format_metrics(warm)
    assert strat.sort_value(warm) == -999.0
    assert not bool(strat.buy_signal(warm).any())


def test_zone_hook():
    df_fire, _ = firing_frame()
    strat = BBPivotWashoutDaily()
    out_fire = strat.compute(df_fire)
    zone = strat.detect_zone_breakout(out_fire)
    assert zone["broke_out"] is True
    assert zone["direction"] == "LONG"
    assert abs(zone["stop_loss"] - float(df_fire["Low"].iloc[-1])) < 1e-9

    closes = base_flat()
    b = bb_lo(closes)
    df_wide = make_frame(closes)
    df_wide = place_prev_p(df_wide, b * 1.02)
    df_wide = trigger(df_wide, open_=b + 0.2, high=b + 1.5, low=96.5)
    zone2 = strat.detect_zone_breakout(strat.compute(df_wide))
    assert zone2["broke_out"] is False and zone2["stop_loss"] is None


def test_hourly_prev_day_shelf():
    """60m variant: pivots = previous SESSION's daily OHLC, constant all day."""
    idx = (
        pd.date_range("2026-03-02 09:15", periods=12, freq="h").tolist()
        + pd.date_range("2026-03-03 09:15", periods=8, freq="h").tolist()
    )
    closes = [100.0] * 19 + [97.0]
    b = bb_lo(closes)
    df = make_frame(closes, index=pd.DatetimeIndex(idx))

    # Day-1 aggregate shelf: agg H=100, agg C=100 => agg L = 3*bb - 200
    agg_l = 3 * b - 200.0
    assert agg_l < 100.0, "shelf low must sit under the closes"
    for i in range(12):
        set_col(df, i, "High", 100.0)
        set_col(df, i, "Low", agg_l)
    # Day-2 trigger (last bar)
    df = trigger(df, open_=b + 0.2, high=b + 1.5, low=96.5)
    check_ohlc(df)

    strat = BBPivotWashout60()
    assert strat.cfg.pivot_source == "prev_day", "60m must use daily pivots"
    out = strat.compute(df)
    buy = strat.buy_signal(out)

    assert bool(buy.iloc[-1]), "hourly washout at the daily shelf must fire"
    day1, day2 = out.index.normalize() == out.index[0].normalize(), \
        out.index.normalize() == out.index[-1].normalize()
    assert np.isnan(out.loc[day1, "PIV_VAL"]).all(), \
        "first session has no previous-session pivot"
    assert not bool(buy[day1].any()), "no signal without a shelf"
    assert out.loc[day2, "PIV_VAL"].nunique() == 1, \
        "the shelf must be constant across the whole session"
    assert abs(float(out["PIV_VAL"].iloc[-1]) - b) < 1e-6


def test_registry_wiring():
    from strategies.registry import STRATEGY_REGISTRY, get_strategy

    e1 = get_strategy("bb_pivot_washout")
    e2 = get_strategy("bb_pivot_washout_60")
    assert e1 is not None and e2 is not None, "both test keys must register"
    assert e1["timeframe"] == "daily"
    assert e2["timeframe"] == "60min"
    assert e1["strategy_obj"].key == "bb_pivot_washout"
    assert e2["strategy_obj"].key == "bb_pivot_washout_60"
    assert e1["strategy_obj"].cfg.pivot_source == "prev_bar"
    assert e2["strategy_obj"].cfg.pivot_source == "prev_day"
    assert e1["name"] == "BB-Pivot Washout"
    assert e2["name"] == "BB-Pivot Washout 60"
    assert "bb_pivot_washout" in STRATEGY_REGISTRY
    assert callable(e1["compute_func"]) and callable(e1["buy_condition"])


def test_engine_signal_row():
    """The engine can consume the strategy end-to-end (row contract)."""
    import engine
    from storage.db import SCAN_COLUMNS

    df, _ = firing_frame()
    strat = BBPivotWashoutDaily()
    row = engine._signal_row(
        strat, df, "NSE:TEST-EQ", "TEST_1", datetime.datetime.now(), 4
    )
    assert set(row) == set(SCAN_COLUMNS) | {"sort_value"}, (
        f"row keys drifted: extra={set(row) - set(SCAN_COLUMNS) - {'sort_value'}} "
        f"missing={set(SCAN_COLUMNS) - set(row)}"
    )
    assert row["signal"] == "BUY"
    assert row["ticker"] == "TEST"
    assert row["current_price"] == 97.0
    assert abs(row["weekly_change_pct"] - (-3.0)) < 1e-9
    assert row["zone_breakout"] is True
    assert row["breakout_direction"] == "LONG"
    assert row["stop_loss_price"] == float(df["Low"].iloc[-1])
    assert row["key_metrics"].startswith("SETUP")
    assert row["hist_signal_count"] == 0  # only the live bar fires
    assert isinstance(row["sort_value"], float)


TESTS = [
    ("pivot formulas (P/R/S textbook values)", test_pivot_formulas),
    ("canonical setup fires (gap ~0%, washout)", test_setup_fires),
    ("nearest-line selection picks S1 when closer", test_nearest_line_is_s1),
    ("wide gap (>=2%) rejected", test_wide_gap_rejected),
    ("0.49% fires / 0.60% rejected (both sides)", test_threshold_boundaries),
    ("open far from band rejected", test_open_far_rejected),
    ("close above band rejected (washout rule)", test_close_above_band_rejected),
    ("close == band strictly rejected", test_equal_close_strict_boundary),
    ("marginal washout fires", test_small_dip_fires),
    ("no lookahead leakage", test_no_future_leakage),
    ("long-only: SELL never fires", test_sell_never_fires),
    ("metrics text + tighter-confluence ranking", test_metrics_and_ranking),
    ("zone hook (LONG + stop = trigger low)", test_zone_hook),
    ("60m: previous-session shelf constant all day", test_hourly_prev_day_shelf),
    ("registry wiring (keys, timeframes, pivot sources)", test_registry_wiring),
    ("engine _signal_row contract (SCAN_COLUMNS)", test_engine_signal_row),
]


def main() -> None:
    print("BB-Pivot Washout — test bench")
    print("-" * 60)
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as exc:  # noqa: BLE001 — report every failure
            FAILURES.append((name, exc))
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
    print("-" * 60)
    total = len(TESTS)
    print(f"{total - len(FAILURES)}/{total} passed")
    if FAILURES:
        sys.exit(1)


if __name__ == "__main__":
    main()
