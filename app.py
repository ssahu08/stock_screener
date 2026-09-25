"""
Streamlit Dashboard — control panel for the screener
=====================================================

Run it:
    streamlit run app.py

What you can do here (no terminal commands needed):
    - Click "Run Scan" to execute a full market scan
    - Pick strategies / universe / price range in the sidebar
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
from strategies.registry import get_strategy, list_strategies


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


def _render_scan(res: dict) -> None:
    """BUY table + CSV download + full-results expander for ONE scan."""
    st.subheader(
        f"🚀 BUY Signals — top {len(res['top_buys'])} of {res['buy_count']}"
    )

    buys = buys_frame(res["top_buys"])
    if buys.empty:
        st.info(
            "⚪ No BUY signals in this scan. Try a later run — "
            "signals appear on bar closes."
        )
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
                    "Indicators", width="large"
                ),
                "weekly_change_pct": st.column_config.NumberColumn(
                    "1W %", format="%+.2f%%", width="small"
                ),
                "hist_signal_count": st.column_config.NumberColumn(
                    "Hist sigs", format="%d", width="small",
                    help="Times this setup fired in the scanned history",
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
            file_name=f"buy_signals_{res['scan_id']}.csv",
            mime="text/csv",
        )

    # Full scan table in an expander
    with st.expander(f"🗂 All {res['scanned']} evaluated stocks (incl. HOLD/SELL)"):
        all_df = pd.DataFrame(res["all_results"])
        show = [c for c in [
            "ticker", "signal", "current_price", "key_metrics",
            "weekly_change_pct", "hist_signal_count", "hist_avg_return",
        ] if c in all_df.columns]
        st.dataframe(all_df[show], width="stretch", hide_index=True)


def _render_ipo_tab() -> None:
    """Event-driven IPO tab: fetch from Apify, score, display table with freshness & cross-check."""
    from strategies.ipo_listing import IPOListingGains, VerifiedListingsCache
    from config.keys import APIFY_TOKEN

    st.subheader("📋 IPO Listing Gains — Live Tracker")

    if not APIFY_TOKEN:
        st.warning(
            "**Apify token not configured.**\n\n"
            "1. Get your free token from https://console.apify.com/account/integrations\n"
            "2. Add it to `config/keys.py` as `APIFY_TOKEN = \"your_token\"`\n"
            "3. Restart the dashboard."
        )
        return

    ipo_strat = IPOListingGains()

    # --- Controls ---
    col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
    with col1:
        view = st.selectbox(
            "View",
            options=["live_upcoming_listed", "live_upcoming"],
            format_func=lambda v: "All (live + upcoming + listed)" if v == "live_upcoming_listed" else "Live + upcoming only",
            index=0,
        )
    with col2:
        category = st.selectbox(
            "Category",
            options=["all", "mainboard", "sme"],
            format_func=lambda c: c.capitalize() if c != "all" else "All",
            index=0,
        )
    with col3:
        refresh = st.button("🔄 Refresh IPO Data", type="primary", width="stretch")
    with col4:
        st.caption("⚠️ **Apify data lags ~2–4 weeks for listed IPOs.** Verify on NSE/BSE before applying.")

    # --- Fetch ---
    if "ipo_data" not in st.session_state or refresh:
        with st.spinner("Fetching IPOs from Apify, scoring & cross-checking NSE…"):
            try:
                cat = None if category == "all" else category
                result = ipo_strat.fetch_and_score(view=view, category=cat)
                st.session_state["ipo_data"] = result["ipos"]
                st.session_state["ipo_meta"] = result["meta"]
                st.session_state["ipo_fetched_at"] = pd.Timestamp.now().strftime("%H:%M:%S")
            except Exception as e:
                st.error(f"Failed to fetch IPO data: {e}")
                st.session_state["ipo_data"] = []
                st.session_state["ipo_meta"] = {}

    ipo_list = st.session_state.get("ipo_data", [])
    meta = st.session_state.get("ipo_meta", {})

    if not ipo_list:
        st.info("No IPO data yet — click **Refresh IPO Data**.")
        return

    # --- Freshness / Meta Banner ---
    freshness = meta.get("apify_freshness_utc")
    fetched_at = st.session_state.get("ipo_fetched_at", "—")
    verified_count = meta.get("verified_count", 0)
    nse_active_count = meta.get("nse_active_count", 0)

    fb1, fb2, fb3, fb4, fb5 = st.columns(5)
    fb1.metric("Total IPOs", meta.get("total", len(ipo_list)))
    fb2.metric("User-verified ✅", verified_count)
    fb3.metric("NSE active cross-check", nse_active_count)
    fb4.caption(f"Apify data as of: {freshness[:16].replace('T', ' ') if freshness else '—'} UTC")
    fb5.caption(f"Dashboard refreshed: {fetched_at}")

    # --- Summary metrics ---
    listed = [i for i in ipo_list if i["status"] == "LISTED"]
    upcoming = [i for i in ipo_list if i["status"] in ("SUBSCRIPTION_OPEN", "ALLOTMENT_DONE")]
    drhp = [i for i in ipo_list if i["status"] == "DRHP_FILED"]
    verified = [i for i in ipo_list if i.get("verified_source")]

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("User-verified Listed ✅", len(verified))
    m2.metric("Apify LISTED (may lag)", len([i for i in ipo_list if i["status"] == "LISTED"]))
    m3.metric("Subscription open", len(upcoming))
    m4.metric("DRHP filed", len(drhp))
    if listed:
        avg_gain = sum(i["listing_gain_pct"] or 0 for i in listed if i.get("listing_gain_pct")) / max(1, len([i for i in listed if i.get("listing_gain_pct")]))
        m5.metric("Avg gain (tracked)", f"{avg_gain:+.1f}%")
    else:
        m5.metric("Avg gain", "—")

    st.divider()

    # --- NSE Active IPOs Cross-Check Section ---
    with st.expander("🔍 NSE Active IPOs (Official Cross-Check)", expanded=False):
        nse_client = IPOListingGains().get_nse_active_ipos()
        if nse_client:
            nse_df = pd.DataFrame(nse_client)
            st.dataframe(
                nse_df[[c for c in ["company", "symbol", "series", "issue_price", "start_date", "end_date", "status"] if c in nse_df.columns]],
                width="stretch", hide_index=True,
                column_config={
                    "company": st.column_config.TextColumn("Company", width="large"),
                    "symbol": st.column_config.TextColumn("Symbol", width="small"),
                    "series": st.column_config.TextColumn("Series", width="small"),
                    "issue_price": st.column_config.TextColumn("Issue Price", width="small"),
                    "start_date": st.column_config.TextColumn("Open", width="small"),
                    "end_date": st.column_config.TextColumn("Close", width="small"),
                    "status": st.column_config.TextColumn("Status", width="small"),
                },
            )
            st.caption(f"Source: NSE India Official API · {len(nse_client)} active IPOs")
        else:
            st.info("No active IPOs on NSE currently, or API unavailable.")

    st.divider()

    # --- Main IPO Table ---
    df = pd.DataFrame(ipo_list)

    # Add NSE cross-check columns
    df["nse_match"] = df.apply(
        lambda r: "✅ Active on NSE" if r.get("nse_active_status") == "Active"
        else ("⚠️ NSE: " + str(r.get("nse_active_status")) if r.get("nse_active_status") else "—"),
        axis=1
    )

    # Reorder columns: decision-critical first
    display_cols = [
        "company",              # What stock
        "status",               # Where in lifecycle
        "recommendation",       # STRONG_APPLY / APPLY / TRACK / AVOID
        "score",                # 0-100 composite
        "expected_gain_low_pct", "expected_gain_high_pct",  # Expected gain range
        "gmp_pct", "gmp",       # Grey market premium (key predictor)
        "issue_price",          # Entry price
        "subscription_x",       # Overall demand
        "qib_x",                # Smart money demand
        "issue_size_cr",        # Smaller = more pop potential
        "category",             # mainboard vs sme
        "nse_match",            # NSE cross-check
        "verified_source",      # User-verified badge
        "sub_open", "sub_close", # Subscription window
        "listing_date", "days_to_listing",  # Timeline
        "breakdown",            # Score component breakdown (technical)
    ]
    df = df[[c for c in display_cols if c in df.columns]]

    # Color-code recommendation
    def rec_color(val):
        colors = {
            "STRONG_APPLY": "background-color: #d4edda; color: #155724",
            "APPLY": "background-color: #d4edda; color: #155724",
            "TRACK": "background-color: #fff3cd; color: #856404",
            "AVOID": "background-color: #f8d7da; color: #721c24",
        }
        return colors.get(val, "")

    def verified_color(val):
        if val and val != "—":
            return "background-color: #d4edda; color: #155724; font-weight: bold"
        return ""

    def nse_color(val):
        if "✅" in str(val):
            return "background-color: #d4edda; color: #155724"
        if "⚠️" in str(val):
            return "background-color: #fff3cd; color: #856404"
        return ""

    st.dataframe(
        df.style.map(rec_color, subset=["recommendation"])
               .map(verified_color, subset=["verified_source"])
               .map(nse_color, subset=["nse_match"]),
        width="stretch",
        hide_index=True,
        column_config={
            "company": st.column_config.TextColumn("Company", width="large"),
            "status": st.column_config.TextColumn("Status", width="medium"),
            "recommendation": st.column_config.TextColumn("Rec", width="medium"),
            "score": st.column_config.NumberColumn("Score", format="%.1f", width="small"),
            "expected_gain_low_pct": st.column_config.NumberColumn("Gain Low %", format="%+.1f%%", width="small"),
            "expected_gain_high_pct": st.column_config.NumberColumn("Gain High %", format="%+.1f%%", width="small"),
            "gmp_pct": st.column_config.NumberColumn("GMP %", format="%+.1f%%", width="small"),
            "gmp": st.column_config.NumberColumn("GMP ₹", format="₹%.2f", width="small"),
            "issue_price": st.column_config.NumberColumn("Issue ₹", format="₹%.2f", width="small"),
            "subscription_x": st.column_config.NumberColumn("Sub ×", format="%.1f", width="small"),
            "qib_x": st.column_config.NumberColumn("QIB ×", format="%.1f", width="small"),
            "issue_size_cr": st.column_config.NumberColumn("Size (Cr)", format="%.1f", width="small"),
            "category": st.column_config.TextColumn("Cat", width="small"),
            "nse_match": st.column_config.TextColumn("NSE Check", width="medium"),
            "verified_source": st.column_config.TextColumn("Verified ✅", width="medium"),
            "sub_open": st.column_config.TextColumn("Open", width="small"),
            "sub_close": st.column_config.TextColumn("Close", width="small"),
            "listing_date": st.column_config.TextColumn("Listing", width="small"),
            "days_to_listing": st.column_config.NumberColumn("Days", format="%d", width="small"),
            "breakdown": st.column_config.TextColumn("Breakdown", width="large"),
        },
    )

    # --- Mark as Listed (User Verification) ---
    st.divider()
    st.subheader("✅ Mark IPO as Listed (User Verification)")
    st.caption("If you confirm an IPO has listed (check NSE/BSE), add it here to override Apify's stale status.")

    v1, v2, v3, v4, v5 = st.columns([2, 1, 1, 1, 1])
    with v1:
        # Only show IPOs not yet verified
        unverified = [i for i in ipo_list if not i.get("verified_source")]
        verify_options = {f"{i['company']} ({i['ipo_id']})": i['ipo_id'] for i in unverified}
        if verify_options:
            selected = st.selectbox("Select IPO to mark as listed", options=list(verify_options.keys()))
            selected_id = verify_options[selected]
        else:
            selected_id = None
            st.info("All IPOs already verified or none available.")

    with v2:
        list_date = st.date_input("Listing date")
    with v3:
        list_price = st.number_input("Listing price (₹)", min_value=0.0, step=0.01, format="%.2f")
    with v4:
        gain_pct = st.number_input("Gain %", step=0.1, format="%.1f")
    with v5:
        if selected_id and st.button("✅ Save Verification", type="primary"):
            ipo_strat.verify_listing(selected_id, list_date.isoformat(), list_price, gain_pct)
            st.success(f"Saved! Refresh to see updated status.")
            st.rerun()

    # Show current verified list
    verified_list = [i for i in ipo_list if i.get("verified_source")]
    if verified_list:
        with st.expander("📋 User-Verified Listings", expanded=False):
            vdf = pd.DataFrame(verified_list)
            vcols = ["company", "listing_date", "listing_price", "listing_gain_pct", "verified_source"]
            st.dataframe(vdf[[c for c in vcols if c in vdf.columns]], width="stretch", hide_index=True)
            if st.button("🗑️ Clear All Verifications"):
                cache = VerifiedListingsCache()
                for v in verified_list:
                    cache.remove(v["ipo_id"])
                st.success("Cleared. Refresh to reload.")
                st.rerun()

    # Download
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "📥 Download IPO Tracker (CSV)",
        data=csv,
        file_name=f"ipo_tracker_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

    # Listing performance section
    if listed:
        st.divider()
        st.subheader("📊 Recently Listed — Actual Gains (from Apify)")
        perf_df = pd.DataFrame(listed)
        perf_cols = ["company", "issue_price", "listing_price", "listing_gain_pct", "listing_date", "gmp_at_listing"]
        perf_df = perf_df[[c for c in perf_cols if c in perf_df.columns]]
        st.dataframe(
            perf_df.style.map(lambda v: "color: green" if isinstance(v, (int, float)) and v > 0 else ("color: red" if isinstance(v, (int, float)) and v < 0 else ""), subset=["listing_gain_pct"]),
            width="stretch",
            hide_index=True,
        )


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

strategy_keys = st.sidebar.multiselect(
    "Strategies (pick one or more)",
    options=list(strategies.keys()),
    default=list(strategies.keys()),
    format_func=lambda k: strategies[k],
)
if not strategy_keys:  # deselecting everything must not break the app
    strategy_keys = [list(strategies.keys())[0]]
st.sidebar.caption(
    "Bench tip: data is fetched once per stock and shared by all "
    "selected strategies — multi runs cost barely more than one."
)

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
# .get() fallback: a long-running Streamlit process can hold a stale
# registry entry from before an edit (KeyError: 'timeframe' seen live).
_tfs = sorted({(get_strategy(k) or {}).get("timeframe", "weekly")
               for k in strategy_keys})
st.caption(
    f"Strategies: **{', '.join(strategies[k] for k in strategy_keys)}** · "
    f"timeframe: {', '.join(_tfs)} · "
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


# Separate event-driven (IPO) from regular scan strategies
ipo_selected = "ipo_listing_gains" in strategy_keys
scan_keys = [k for k in strategy_keys if k != "ipo_listing_gains"]

# --- Run scan (regular strategies only) -------------------------------
if run_clicked and scan_keys:
    with st.spinner(
        f"Scanning {'full NSE' if universe == 'full' else 'Nifty sample'} "
        f"— fetching data, computing indicators…"
    ):
        results = run_screener(
            strategy_key=",".join(scan_keys),
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


# --- Results: regular scan strategies ---------------------------------
if results and not results.get("error"):
    st.divider()

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Scanned", results["scanned"])
    m2.metric("BUY signals", results["buy_count"])
    m3.metric("SELL signals", results["sell_count"])
    m4.metric("HOLD", results["hold_count"])
    m5.metric("Scan time", results["scan_time"].strftime("%H:%M:%S"))

    scans = results["scans"] if results.get("multi") else [results]
    if len(scans) > 1:
        st.caption(
            f"Headline numbers are the TOTAL across {len(scans)} strategies "
            "— each tab shows that strategy's own scan."
        )
    tabs = st.tabs([f"🔥 {s['strategy']}" for s in scans])
    for tab, res in zip(tabs, scans):
        with tab:
            _render_scan(res)

# --- Results: IPO Listing Gains (event-driven) ------------------------
if ipo_selected:
    st.divider()
    _render_ipo_tab()


# --- History + stats (from DuckDB) ----------------------------------
st.divider()
st.subheader("📜 Scan History & Stats")

stats_key = st.selectbox(
    "Stats & history for",
    options=list(strategies.keys()),
    format_func=lambda k: strategies[k],
    index=list(strategies.keys()).index(strategy_keys[0]),
)
stats = get_strategy_stats(strategies[stats_key])
if stats:
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Stored scans", stats.get("total_scans", 0))
    s2.metric("BUY rows", stats.get("buy_signals", 0))
    s3.metric("Evaluated BUYs", stats.get("evaluated_buys", 0))
    s4.metric("Avg 4W fwd", f"{stats.get('avg_forward_4w_return', 0):+.2f}%")
    s5.metric("Win rate", f"{stats.get('win_rate_pct', 0):.1f}%")
    st.caption(f"Last scan recorded: {stats.get('last_scan', 'Never')}")

hist = get_scan_history(strategy_name=strategies[stats_key], limit=200)
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
