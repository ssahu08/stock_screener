"""
Streamlit Dashboard — control panel for the screener
=====================================================

Run it:
    streamlit run app.py

What you can do here (no terminal commands needed):
    - Click "Run Scan" to execute a full market scan
    - Pick strategy / universe / price range in the sidebar
    - View BUY signals, download CSV, browse scan history
    - See strategy performance stats

Login (Option A) still happens ONCE in your terminal:
    python core/auth.py
The dashboard only checks whether today's token exists —
it never asks for credentials or tokens.
"""

import os
import sys

import pandas as pd
import streamlit as st

# Project root on path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

st.set_page_config(
    page_title="NSE Stock Screener",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

from config.settings import UNIVERSE_SETTINGS
from core.auth import read_valid_token
from engine import run_screener
from storage.db import get_scan_history, get_strategy_stats
from strategies.registry import list_strategies


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def token_status() -> tuple[bool, str]:
    """Check Option A token validity without prompting."""
    if read_valid_token():
        return True, "✅ Fyers token valid for today"
    return False, "🔐 No valid token — run `python core/auth.py` once"


def buys_frame(rows: list[dict]) -> pd.DataFrame:
    """BUY rows -> tidy DataFrame for display/download."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    cols = [
        "ticker", "current_price", "key_metrics", "weekly_change_pct",
        "hist_signal_count", "hist_avg_return", "hist_signal_dates",
        "zone_breakout", "breakout_direction", "stop_loss_price",
        "profit_taking_warning",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols]


# ------------------------------------------------------------------
# Sidebar — scan configuration
# ------------------------------------------------------------------

st.sidebar.title("📈 Screener Controls")

ok, token_msg = token_status()
if not ok:
    st.sidebar.warning(token_msg)

strategies = list_strategies()
if not strategies:
    st.sidebar.error("No strategies registered — see strategies/registry.py")
    st.stop()

strategy_label = st.sidebar.selectbox(
    "Strategy",
    options=list(strategies.keys()),
    format_func=lambda k: strategies[k],
)
strategy_name = strategies[strategy_label]

universe = st.sidebar.radio(
    "Universe",
    options=["full", "nifty50"],
    format_func=lambda u: "Full NSE (~2,300 stocks)" if u == "full" else "Nifty 50 sample (fast test)",
    index=0,
)
st.sidebar.caption("Full NSE takes ~2–4 min; Nifty sample ~30 sec")

c1, c2 = st.sidebar.columns(2)
min_price = c1.number_input(
    "Min ₹", value=UNIVERSE_SETTINGS["min_price"], step=50.0
)
max_price = c2.number_input(
    "Max ₹", value=UNIVERSE_SETTINGS["max_price"], step=100.0
)

send_alert = st.sidebar.checkbox("Send Telegram alert", value=True)

run_clicked = st.sidebar.button(
    "🚀 Run Scan",
    type="primary",
    width="stretch",
    disabled=not ok,
    help="Runs the scan using today's saved token" if ok
         else "First run: python core/auth.py",
)


# ------------------------------------------------------------------
# Main area
# ------------------------------------------------------------------

st.title("NSE Stock Screener")
st.caption(
    f"Strategy: **{strategy_name}** · Weekly timeframe · "
    "signals are candidates for review, not trading advice"
)

if not ok:
    st.info(
        "**Step 1 — authenticate once (Option A):**\n\n"
        "Open your terminal in the project folder and run:\n\n"
        "```bash\npython core/auth.py\n```\n\n"
        "Log in via the browser, paste the auth code, then refresh this page."
    )
    st.stop()


# --- Run scan -------------------------------------------------------
if run_clicked:
    with st.spinner(
        f"Scanning {'full NSE' if universe == 'full' else 'Nifty sample'} "
        f"— fetching data, computing indicators…"
    ):
        results = run_screener(
            strategy_key=strategy_label,
            universe=universe,
            min_price=float(min_price),
            max_price=float(max_price),
            send_alert=send_alert,
        )
    st.session_state["last_results"] = results

results = st.session_state.get("last_results")

if results and results.get("error") == "auth_required":
    st.error(results["message"])
    st.stop()


# --- Results --------------------------------------------------------
if results and not results.get("error"):
    st.divider()

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Scanned", results["scanned"])
    m2.metric("BUY signals", results["buy_count"])
    m3.metric("SELL signals", results["sell_count"])
    m4.metric("HOLD", results["hold_count"])
    m5.metric("Scan time", results["scan_time"].strftime("%H:%M:%S"))

    st.subheader(f"🚀 BUY Signals — top {len(results['top_buys'])} of {results['buy_count']}")

    buys = buys_frame(results["top_buys"])
    if buys.empty:
        st.info("⚪ No BUY signals in this scan. Try a later run — signals appear on weekly closes.")
    else:
        st.dataframe(
            buys,
            width="stretch",
            hide_index=True,
            column_config={
                "ticker": st.column_config.TextColumn("Ticker", width="small"),
                "current_price": st.column_config.NumberColumn(
                    "Price", format="₹%.2f", width="small"
                ),
                "key_metrics": st.column_config.TextColumn(
                    "Indicators (RSI / WMA / EMA)", width="large"
                ),
                "weekly_change_pct": st.column_config.NumberColumn(
                    "1W %", format="%+.2f%%", width="small"
                ),
                "hist_signal_count": st.column_config.NumberColumn(
                    "Hist sigs", format="%d", width="small",
                    help="Times this setup fired in the last ~52 weeks",
                ),
                "hist_avg_return": st.column_config.NumberColumn(
                    "Avg 4W fwd", format="%+.2f%%", width="small",
                    help="Average forward 4-week return after past signals",
                ),
                "hist_signal_dates": st.column_config.TextColumn(
                    "Past signal dates", width="large"
                ),
                "zone_breakout": st.column_config.CheckboxColumn("Zone break"),
                "breakout_direction": st.column_config.TextColumn("Dir", width="tiny"),
                "stop_loss_price": st.column_config.NumberColumn(
                    "Stop (zone)", format="₹%.2f", width="small",
                    help="Low (longs) / high (shorts) of the candle where the "
                         "red line first broke out of the zone",
                ),
                "profit_taking_warning": st.column_config.CheckboxColumn(
                    "EMA≈RSI warn",
                    help="Green line touching RSI = early profit-taking warning",
                ),
            },
        )

        csv = buys.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Download BUY signals (CSV)",
            data=csv,
            file_name=f"buy_signals_{results['scan_id']}.csv",
            mime="text/csv",
        )

    # Full scan table in an expander
    with st.expander(f"🗂 All {results['scanned']} evaluated stocks (incl. HOLD/SELL)"):
        all_df = pd.DataFrame(results["all_results"])
        show = [c for c in [
            "ticker", "signal", "current_price", "key_metrics",
            "weekly_change_pct", "hist_signal_count", "hist_avg_return",
        ] if c in all_df.columns]
        st.dataframe(all_df[show], width="stretch", hide_index=True)


# --- History + stats (from DuckDB) ----------------------------------
st.divider()
st.subheader("📜 Scan History & Stats")

stats = get_strategy_stats(strategy_name)
if stats:
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Stored scans", stats.get("total_scans", 0))
    s2.metric("BUY rows", stats.get("buy_signals", 0))
    s3.metric("Evaluated BUYs", stats.get("evaluated_buys", 0))
    s4.metric("Avg 4W fwd", f"{stats.get('avg_forward_4w_return', 0):+.2f}%")
    s5.metric("Win rate", f"{stats.get('win_rate_pct', 0):.1f}%")
    st.caption(f"Last scan recorded: {stats.get('last_scan', 'Never')}")

hist = get_scan_history(strategy_name=strategy_name, limit=200)
if hist.empty:
    st.caption("No history yet — run a scan.")
else:
    filt = st.radio(
        "Show", ["BUY only", "All signals"],
        horizontal=True, index=0,
    )
    view = hist if filt == "All signals" else hist[hist["signal"] == "BUY"]
    st.dataframe(
        view[[
            "scan_time", "ticker", "signal", "current_price", "key_metrics",
            "weekly_change_pct", "hist_avg_return",
        ]],
        width="stretch",
        hide_index=True,
    )
