"""
Streamlit Dashboard — NSE Stock Screener
==========================================

Two kinds of categories:
  SCAN   — runs the NSE scan engine (Long Term, Breakout/BTST, Intraday)
  PANEL  — standalone panels, NEVER scan NSE (Global Macro, Event-Driven)

Run: streamlit run app.py        (or deploy to Streamlit Community Cloud)
Password:  DASHBOARD_PASSWORD from .streamlit/secrets.toml (never in git)
Fyers login (daily, ~6:30 AM IST expiry): in-app login box below,
  or on a laptop: python core/auth.py
"""

import os
import sys

# Windows consoles default to cp1252 — emoji in logs would crash scans.
# UTF-8 output never raises UnicodeEncodeError.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pandas as pd
import streamlit as st

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

st.set_page_config(
    page_title="NSE Stock Screener",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ------------------------------------------------------------------
# PASSWORD GATE — runs before any other rendering (and before the heavy
# imports), so visitors only ever see the login form.
# Password lives in .streamlit/secrets.toml / Streamlit Cloud secrets,
# never in git.
# ------------------------------------------------------------------
import hmac

from config.keys import DASHBOARD_PASSWORD

if not DASHBOARD_PASSWORD:
    st.error(
        "🔒 **Dashboard is locked:** `DASHBOARD_PASSWORD` is not configured.\n\n"
        "Add `DASHBOARD_PASSWORD = \"...\"` to `.streamlit/secrets.toml` (local) "
        "or to *App settings → Secrets* on Streamlit Community Cloud, then restart."
    )
    st.stop()

if not st.session_state.get("unlocked"):
    st.title("🔐 NSE Stock Screener")
    st.caption("Enter the dashboard password to continue.")
    with st.form("password_gate"):
        _pw = st.text_input("Password", type="password")
        _submit = st.form_submit_button("Unlock", type="primary")
    if _submit:
        if hmac.compare_digest(_pw.encode("utf-8"), DASHBOARD_PASSWORD.encode("utf-8")):
            st.session_state["unlocked"] = True
            st.rerun()
        else:
            st.error("Wrong password.")
    st.stop()

from config.settings import UNIVERSE_SETTINGS
from core.auth import read_valid_token, build_auth_url, exchange_auth_code
from engine import run_screener, get_fyers_client

# Scheduled Telegram jobs: SOD 09:00 IST (US close + NIFTY + expiry reminder)
# and EOD 15:45 IST (NIFTY summary).  Idempotent — one thread per process.
# Disable with SCREENER_SCHEDULER_DISABLE=1 (tests).
try:
    from core.scheduler import start_scheduler
    start_scheduler()
except Exception as _sched_exc:
    st.sidebar.warning(f"Scheduler not running: {_sched_exc}")
from storage.db import get_scan_history, get_strategy_stats
from strategies.registry import list_strategies


# ------------------------------------------------------------------
# Categories — kind = "scan" (NSE engine) or "panel" (standalone)
# ------------------------------------------------------------------

CATEGORIES = {
    "long_term": {
        "label": "📈 Long Term",
        "kind": "scan",
        "strategies": ["hilega_milega"],
        "description": "Weekly momentum, trend following — 2-week live trial",
    },
    "breakout": {
        "label": "🚀 Breakout / BTST",
        "kind": "scan",
        "strategies": [
            "volcano_watch", "volcano_blast", "volcano_snap",
            "coil_break", "darvas_box",
        ],
        "description": "Daily compression → expansion breakouts",
    },
    "intraday": {
        "label": "⚡ Intraday",
        "kind": "scan",
        "strategies": [
            "volcano_blast_60", "expiry_credit_spread_nifty",
            "expiry_credit_spread_banknifty",
        ],
        "description": "Same-day entry/exit: 60-min volatility, expiry credit spreads",
    },
    "global_macro": {
        "label": "🌍 Global Macro",
        "kind": "panel",
        "strategies": ["index_follower"],
        "description": "US close ±1% → 4 Indian ETFs. No NSE scan — checks only MON100, MONQ50, MASPTOP50, MAFANG.",
    },
    "event_driven": {
        "label": "📋 Event-Driven",
        "kind": "panel",
        "strategies": ["ipo_listing_gains"],
        "description": "IPO listing gains tracker (Apify + NSE cross-check)",
    },
    # TEST BENCH — feature/bb-pivot-test branch only; never merged to
    # production until the owner validates it on real charts.
    "test_bench": {
        "label": "🧪 Test Bench",
        "kind": "scan",
        "strategies": ["bb_pivot_washout", "bb_pivot_washout_60"],
        "description": (
            "TEST ONLY — Bollinger-Pivot confluence washout "
            "(<=0.5% gap, close below band). Unvalidated: charts/paper only."
        ),
    },
}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def token_status() -> tuple[bool, str]:
    if read_valid_token():
        return True, "✅ Fyers token valid for today"
    return False, "🔐 Not logged in — use the Fyers login box below"


def render_fyers_login() -> None:
    """Mobile-friendly Fyers login: open the link, paste the redirect URL.

    The auth_code is pasted by the user directly into their own dashboard
    and exchanged server-side — it never passes through chat or logs.
    """
    st.warning("🔐 Daily Fyers login required — access tokens expire ~6:30 AM IST.")
    st.markdown(
        "**Log in from this device (takes 30 seconds):**\n\n"
        "1. Tap **Open Fyers Login** and log in on the Fyers page\n"
        "2. **Copy the full URL** from the browser address bar "
        "(it contains `auth_code=`)\n"
        "3. Paste it below and tap **Sign in**"
    )
    st.link_button("🔓 Open Fyers Login", build_auth_url(), type="primary")
    pasted = st.text_input(
        "Paste the redirect URL (or auth_code):",
        key="fyers_paste",
        placeholder="https://trade.fyers.in/api-login/redirect-uri/index.html?auth_code=…",
    )
    if st.button(
        "✅ Sign in", type="primary",
        disabled=not pasted.strip(), key="fyers_submit",
    ):
        with st.spinner("Exchanging auth_code for an access token…"):
            success, message = exchange_auth_code(pasted)
        if success:
            st.success("Logged in! Loading dashboard…")
            st.rerun()
        else:
            st.error(
                f"{message}\n\n"
                "The auth_code expires ~2 minutes after login — copy the URL "
                "right after you land on the redirect page, then try again."
            )


def buys_frame(rows: list[dict]) -> pd.DataFrame:
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
    """Render BUY table + CSV + all-results for ONE strategy scan."""
    st.subheader(f"🚀 BUY Signals — top {len(res['top_buys'])} of {res['buy_count']}")

    buys = buys_frame(res["top_buys"])
    if buys.empty:
        st.info("⚪ No BUY signals in this scan. Signals appear on bar closes — try a later run.")
    else:
        st.dataframe(
            buys,
            width="stretch",
            hide_index=True,
            column_config={
                "ticker": st.column_config.TextColumn("Ticker", width="small"),
                "current_price": st.column_config.NumberColumn("Price", format="₹%.2f", width="small"),
                "key_metrics": st.column_config.TextColumn("Indicators", width="large"),
                "weekly_change_pct": st.column_config.NumberColumn("1W %", format="%+.2f%%", width="small"),
                "hist_signal_count": st.column_config.NumberColumn("Hist sigs", format="%d", width="small",
                    help="Times this setup fired in scanned history"),
                "hist_avg_return": st.column_config.NumberColumn("Avg 4W fwd", format="%+.2f%%", width="small",
                    help="Average forward 4-week return after past signals"),
                "hist_signal_dates": st.column_config.TextColumn("Past signal dates", width="large"),
                "zone_breakout": st.column_config.CheckboxColumn("Zone break"),
                "breakout_direction": st.column_config.TextColumn("Dir", width="tiny"),
                "stop_loss_price": st.column_config.NumberColumn("Stop (zone)", format="₹%.2f", width="small"),
                "profit_taking_warning": st.column_config.CheckboxColumn("EMA≈RSI warn"),
            },
        )
        st.download_button(
            "📥 Download BUY signals (CSV)",
            data=buys.to_csv(index=False).encode("utf-8"),
            file_name=f"buy_signals_{res['scan_id']}.csv",
            mime="text/csv",
        )

    with st.expander(f"🗂 All {res['scanned']} evaluated stocks (incl. HOLD/SELL)"):
        all_df = pd.DataFrame(res["all_results"])
        show = [c for c in [
            "ticker", "signal", "current_price", "key_metrics",
            "weekly_change_pct", "hist_signal_count", "hist_avg_return",
        ] if c in all_df.columns]
        st.dataframe(all_df[show], width="stretch", hide_index=True)


def render_scan_category(cat_key: str) -> None:
    """Show stored scan results filtered to this category's strategies."""
    info = CATEGORIES[cat_key]
    st.caption(info["description"])

    res = st.session_state.get("scan_result")
    if res is None:
        st.info("👈 Click **Run Scan** in the sidebar to scan this category.")
        return
    if res.get("error"):
        st.warning(res.get("message", "Scan failed."))
        return

    scans = res["scans"] if res.get("multi") else [res]
    mine = [s for s in scans if s.get("strategy_key") in info["strategies"]]
    if not mine:
        st.info("The last scan didn't include this category. Click **Run Scan** again.")
        return

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Scanned", max(s["scanned"] for s in mine))
    m2.metric("BUY", sum(s["buy_count"] for s in mine))
    m3.metric("SELL", sum(s["sell_count"] for s in mine))
    m4.metric("HOLD", sum(s["hold_count"] for s in mine))
    m5.metric("Scan Time", res.get("scan_time", mine[0]["scan_time"]).strftime("%H:%M:%S"))

    if len(mine) == 1:
        _render_scan(mine[0])
    else:
        tabs = st.tabs([f"🔥 {s['strategy']}" for s in mine])
        for tab, s in zip(tabs, mine):
            with tab:
                _render_scan(s)


# ------------------------------------------------------------------
# Global Macro panel — ETFs ONLY, never touches NSE scan engine
# ------------------------------------------------------------------

def render_global_macro() -> None:
    from strategies.global_macro.index_follower import IndexFollower
    from strategies.global_macro.config import ETF_UNIVERSE, CAPITAL_PER_TRADE

    st.caption(CATEGORIES["global_macro"]["description"])

    idx = IndexFollower()

    # --- 1. US close + signals -------------------------------------
    if st.button("🔄 Fetch US Close & Signals", type="primary", width="stretch"):
        with st.spinner("Fetching US indices…"):
            st.session_state["gm_plan"] = idx.get_sod_plan()

    plan = st.session_state.get("gm_plan")
    if plan:
        st.markdown("#### 📊 US Indices (previous close)")
        cols = st.columns(4)
        for i, (name, pct) in enumerate(plan["us_indices"].items()):
            with cols[i % 4]:
                delta = f"{pct:+.2f}%" if pct is not None else "N/A"
                st.metric(name, delta, delta_color="normal")

        if plan["signals"]:
            st.markdown("#### 🎯 Trade Signals (buy at 9:15 AM open)")
            for s in plan["signals"]:
                st.write(
                    f"**{s['direction']} {s['etf_symbol']}** — {s['trigger']} "
                    f"| ₹{s['capital']:,} capital"
                )
        else:
            st.info("📭 No signals — no US index moved ±1% or more.")

        if plan["major_alerts"]:
            for a in plan["major_alerts"]:
                st.warning(a["message"])
    else:
        st.caption("Click the button above to fetch yesterday's US close and today's ETF signals.")

    # --- 2. Live ETF quotes (4 symbols only) ------------------------
    st.markdown("#### 📡 ETF Quotes (NSE — 4 symbols only)")
    fyers = get_fyers_client()
    if fyers is None:
        st.warning("No Fyers token — run `python core/auth.py` to see live ETF prices.")
    else:
        try:
            symbols = ",".join(f"NSE:{e.symbol}-EQ" for e in ETF_UNIVERSE)
            resp = fyers.quotes({"symbols": symbols})
            if resp.get("s") == "ok":
                qdf = pd.DataFrame([
                    {
                        "ETF": d["n"].split(":")[-1].replace("-EQ", ""),
                        "LTP ₹": d["v"].get("lp"),
                        "Change %": (
                            round((d["v"]["lp"] - d["v"]["ep"]) / d["v"]["ep"] * 100, 2)
                            if d["v"].get("ep") else None
                        ),
                        "High ₹": d["v"].get("ht"),
                        "Low ₹": d["v"].get("lt"),
                    }
                    for d in resp["d"]
                ])
                st.dataframe(qdf, width="stretch", hide_index=True)
            else:
                st.info("Quotes unavailable right now.")
        except Exception as e:
            st.info(f"Quote fetch failed: {e}")

    # --- 3. Manual trade log ---------------------------------------
    st.divider()
    st.markdown("#### 📝 Trade Log (₹10,000 per trade, exit by discretion)")
    t1, t2, t3, t4, t5 = st.columns([2, 1, 1, 1, 1])
    with t1:
        etf = st.selectbox("ETF", [e.symbol for e in ETF_UNIVERSE], key="gm_etf")
    with t2:
        direction = st.selectbox("Dir", ["BUY", "SELL"], key="gm_dir")
    with t3:
        entry = st.number_input("Entry ₹", min_value=0.0, step=0.01, key="gm_entry")
    with t4:
        exit_price = st.number_input("Exit ₹", min_value=0.0, step=0.01, key="gm_exit")
    with t5:
        st.caption("")  # align
        if st.button("💾 Log", key="gm_log", width="stretch") and entry > 0:
            units = CAPITAL_PER_TRADE / entry
            pnl = (exit_price - entry) * units
            st.session_state.setdefault("gm_trades", []).append({
                "etf": etf, "dir": direction, "entry": entry,
                "exit": exit_price, "pnl": round(pnl),
            })
            st.rerun()

    trades = st.session_state.get("gm_trades", [])
    if trades:
        tdf = pd.DataFrame(trades)
        st.dataframe(tdf, width="stretch", hide_index=True)
        st.metric("Total P&L", f"₹{tdf['pnl'].sum():+,.0f}")
        if st.button("🗑️ Clear log", key="gm_clear"):
            st.session_state["gm_trades"] = []
            st.rerun()


# ------------------------------------------------------------------
# Event-Driven panel (IPO)
# ------------------------------------------------------------------

def render_event_driven() -> None:
    from config.keys import APIFY_TOKEN
    from strategies.ipo_listing import IPOListingGains, VerifiedListingsCache

    st.caption(CATEGORIES["event_driven"]["description"])

    if not APIFY_TOKEN:
        st.warning("⚠️ Apify token not configured. Add to `config/keys.py` and restart.")
        return

    ipo_strat = IPOListingGains()

    col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
    with col1:
        view = st.selectbox(
            "View", ["live_upcoming_listed", "live_upcoming"],
            format_func=lambda v: "All (live+upcoming+listed)" if v == "live_upcoming_listed" else "Live+Upcoming",
        )
    with col2:
        category = st.selectbox(
            "Category", ["all", "mainboard", "sme"],
            format_func=lambda c: c.capitalize() if c != "all" else "All",
        )
    with col3:
        refresh = st.button("🔄 Refresh IPO Data", type="primary", width="stretch")
    with col4:
        st.caption("⚠️ Apify data lags 2–4 weeks for listed IPOs. Verify on NSE/BSE.")

    if "ipo_data" not in st.session_state or refresh:
        with st.spinner("Fetching IPOs from Apify, scoring & NSE cross-check…"):
            try:
                cat = None if category == "all" else category
                result = ipo_strat.fetch_and_score(view=view, category=cat)
                st.session_state["ipo_data"] = result["ipos"]
                st.session_state["ipo_meta"] = result["meta"]
                st.session_state["ipo_fetched_at"] = pd.Timestamp.now().strftime("%H:%M:%S")
            except Exception as e:
                st.error(f"Failed to fetch IPO data: {e}")
                st.session_state["ipo_data"] = []

    ipo_list = st.session_state.get("ipo_data", [])
    meta = st.session_state.get("ipo_meta", {})

    if not ipo_list:
        st.info("No IPO data — click **Refresh IPO Data**.")
        return

    freshness = meta.get("apify_freshness_utc")
    fetched_at = st.session_state.get("ipo_fetched_at", "—")

    fb1, fb2, fb3, fb4, fb5 = st.columns(5)
    fb1.metric("Total IPOs", meta.get("total", len(ipo_list)))
    fb2.metric("User-verified ✅", meta.get("verified_count", 0))
    fb3.metric("NSE active cross-check", meta.get("nse_active_count", 0))
    fb4.caption(f"Apify data: {freshness[:16].replace('T', ' ') if freshness else '—'} UTC")
    fb5.caption(f"Refreshed: {fetched_at}")

    listed = [i for i in ipo_list if i["status"] == "LISTED"]
    upcoming = [i for i in ipo_list if i["status"] in ("SUBSCRIPTION_OPEN", "ALLOTMENT_DONE")]
    drhp = [i for i in ipo_list if i["status"] == "DRHP_FILED"]
    verified = [i for i in ipo_list if i.get("verified_source")]

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Verified Listed ✅", len(verified))
    m2.metric("Apify LISTED (lag)", len(listed))
    m3.metric("Sub Open", len(upcoming))
    m4.metric("DRHP", len(drhp))
    if listed:
        gains = [i["listing_gain_pct"] for i in listed if i.get("listing_gain_pct") is not None]
        m5.metric("Avg Gain", f"{sum(gains) / len(gains):+.1f}%" if gains else "—")
    else:
        m5.metric("Avg Gain", "—")

    st.divider()

    with st.expander("🔍 NSE Active IPOs (Official Cross-Check)", expanded=False):
        nse_client = ipo_strat.get_nse_active_ipos()
        if nse_client:
            nse_df = pd.DataFrame(nse_client)
            st.dataframe(
                nse_df[[c for c in ["company", "symbol", "series", "issue_price",
                                    "start_date", "end_date", "status"] if c in nse_df.columns]],
                width="stretch", hide_index=True,
            )
        else:
            st.info("No active IPOs on NSE or API unavailable.")

    st.divider()

    df = pd.DataFrame(ipo_list)
    df["nse_match"] = df.apply(
        lambda r: "✅ Active on NSE" if r.get("nse_active_status") == "Active"
        else ("⚠️ NSE: " + str(r.get("nse_active_status")) if r.get("nse_active_status") else "—"),
        axis=1,
    )

    display_cols = [
        "company", "status", "recommendation", "score",
        "expected_gain_low_pct", "expected_gain_high_pct",
        "gmp_pct", "gmp", "issue_price", "subscription_x", "qib_x", "issue_size_cr",
        "category", "nse_match", "verified_source",
        "sub_open", "sub_close", "listing_date", "days_to_listing", "breakdown",
    ]
    df = df[[c for c in display_cols if c in df.columns]]

    def rec_color(val):
        colors = {"STRONG_APPLY": "#d4edda", "APPLY": "#d4edda",
                  "TRACK": "#fff3cd", "AVOID": "#f8d7da"}
        return f"background-color: {colors[val]}; color: #155724" if val in colors else ""

    def verified_color(val):
        return "background-color: #d4edda; color: #155724; font-weight: bold" if val and val != "—" else ""

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
        width="stretch", hide_index=True,
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

    st.divider()
    st.markdown("#### ✅ Mark IPO as Listed (User Verification)")
    unverified = [i for i in ipo_list if not i.get("verified_source")]
    if unverified:
        v1, v2, v3, v4, v5 = st.columns([2, 1, 1, 1, 1])
        with v1:
            label = st.selectbox(
                "Select IPO",
                [f"{i['company']} ({i['ipo_id']})" for i in unverified],
                key="ipo_verify",
            )
            selected_id = next(i["ipo_id"] for i in unverified
                               if f"{i['company']} ({i['ipo_id']})" == label)
        with v2:
            list_date = st.date_input("Listing date", key="ipo_ldate")
        with v3:
            list_price = st.number_input("Listing price (₹)", min_value=0.0, step=0.01, format="%.2f", key="ipo_lprice")
        with v4:
            gain_pct = st.number_input("Gain %", step=0.1, format="%.1f", key="ipo_gain")
        with v5:
            st.caption("")
            if st.button("✅ Save", type="primary", key="ipo_save", width="stretch"):
                cache = VerifiedListingsCache()
                cache.set(selected_id, {
                    "listing_date": list_date.isoformat(),
                    "listing_price": list_price,
                    "listing_gain_pct": gain_pct,
                })
                st.success("Saved! Refresh to see updated status.")
                st.rerun()
    else:
        st.info("All IPOs already verified or none available.")

    verified_list = [i for i in ipo_list if i.get("verified_source")]
    if verified_list:
        with st.expander("📋 User-Verified Listings", expanded=False):
            vdf = pd.DataFrame(verified_list)
            st.dataframe(
                vdf[["company", "listing_date", "listing_price",
                     "listing_gain_pct", "verified_source"]],
                width="stretch", hide_index=True,
            )
            if st.button("🗑️ Clear All Verifications", key="ipo_clear"):
                cache = VerifiedListingsCache()
                for v in verified_list:
                    cache.remove(v["ipo_id"])
                st.rerun()

    st.download_button(
        "📥 Download IPO Tracker (CSV)",
        data=df.to_csv(index=False).encode("utf-8"),
        file_name=f"ipo_tracker_{pd.Timestamp.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )

    if listed:
        st.divider()
        st.subheader("📊 Recently Listed — Actual Gains")
        perf_df = pd.DataFrame(listed)
        perf_cols = ["company", "issue_price", "listing_price",
                     "listing_gain_pct", "listing_date", "gmp_at_listing"]
        st.dataframe(
            perf_df[[c for c in perf_cols if c in perf_df.columns]].style.map(
                lambda v: "color: green" if isinstance(v, (int, float)) and v > 0
                else ("color: red" if isinstance(v, (int, float)) and v < 0 else ""),
                subset=["listing_gain_pct"],
            ),
            width="stretch", hide_index=True,
        )


# ==================================================================
# SIDEBAR
# ==================================================================

st.sidebar.title("📈 Screener Controls")

ok, token_msg = token_status()
if not ok:
    st.sidebar.warning(token_msg)

strategies = list_strategies()

st.sidebar.markdown("### 📂 Categories")
selected_categories = {}
for cat_key, cat_info in CATEGORIES.items():
    if not any(s in strategies for s in cat_info["strategies"]):
        continue
    selected_categories[cat_key] = st.sidebar.checkbox(
        cat_info["label"], value=True, help=cat_info["description"],
    )

# Scan strategies ONLY — panels never enter the NSE engine
scan_keys = [
    s
    for cat_key, sel in selected_categories.items()
    if sel and CATEGORIES[cat_key]["kind"] == "scan"
    for s in CATEGORIES[cat_key]["strategies"]
    if s in strategies
]

st.sidebar.divider()

if scan_keys:
    universe = st.sidebar.radio(
        "Universe", ["nifty50", "full"],
        format_func=lambda u: "Nifty 50 (fast)" if u == "nifty50" else "Full NSE (~2,300)",
        index=0,
    )
    st.sidebar.caption("Full NSE ~2–4 min | Nifty 50 ~30 sec")
    c1, c2 = st.sidebar.columns(2)
    min_price = c1.number_input("Min ₹", value=UNIVERSE_SETTINGS["min_price"], step=50.0)
    max_price = c2.number_input("Max ₹", value=UNIVERSE_SETTINGS["max_price"], step=100.0)
    send_alert = st.sidebar.checkbox("Send Telegram alert", value=True)

    run_clicked = st.sidebar.button(
        "🚀 Run Scan", type="primary", width="stretch",
        disabled=not ok,
        help="Scans selected categories" if ok else "Log in via the Fyers box first",
    )
else:
    run_clicked = False
    st.sidebar.info("Only panel categories selected — nothing to scan.")

st.sidebar.divider()
if st.sidebar.button("🔒 Log out", help="Lock the dashboard (password required again)"):
    st.session_state.pop("unlocked", None)
    st.rerun()

# Execute the scan ONCE, before tabs render
if run_clicked:
    with st.spinner(f"Scanning {len(scan_keys)} strategies on {universe}…"):
        st.session_state["scan_result"] = run_screener(
            strategy_key=",".join(scan_keys),
            universe=universe,
            min_price=float(min_price),
            max_price=float(max_price),
            send_alert=send_alert,
        )
    st.rerun()

# ==================================================================
# MAIN
# ==================================================================

st.title("📈 NSE Stock Screener")

if not ok:
    render_fyers_login()
    st.stop()

selected_cats = [
    k for k, sel in selected_categories.items()
    if sel and any(s in strategies for s in CATEGORIES[k]["strategies"])
]

if not selected_cats:
    st.warning("Select at least one category in the sidebar.")
    st.stop()

tabs = st.tabs([CATEGORIES[k]["label"] for k in selected_cats])

for tab, cat_key in zip(tabs, selected_cats):
    with tab:
        kind = CATEGORIES[cat_key]["kind"]
        if kind == "scan":
            render_scan_category(cat_key)
        elif cat_key == "global_macro":
            render_global_macro()
        elif cat_key == "event_driven":
            render_event_driven()

# ==================================================================
# History & Stats
# ==================================================================

st.divider()
st.subheader("📜 Scan History & Stats")

stats_key = st.selectbox(
    "Stats for", options=list(strategies.keys()),
    format_func=lambda k: strategies[k],
)

stats = get_strategy_stats(strategies[stats_key])
if stats:
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("Scans", stats.get("total_scans", 0))
    s2.metric("BUY rows", stats.get("buy_signals", 0))
    s3.metric("Evaluated BUYs", stats.get("evaluated_buys", 0))
    s4.metric("Avg 4W fwd", f"{stats.get('avg_forward_4w_return', 0):+.2f}%")
    s5.metric("Win rate", f"{stats.get('win_rate_pct', 0):.1f}%")
    st.caption(f"Last scan: {stats.get('last_scan', 'Never')}")

hist = get_scan_history(strategy_name=strategies[stats_key], limit=200)
if not hist.empty:
    filt = st.radio("Show", ["BUY only", "All"], horizontal=True)
    view = hist if filt == "All" else hist[hist["signal"] == "BUY"]
    st.dataframe(
        view[["scan_time", "ticker", "signal", "current_price",
              "key_metrics", "weekly_change_pct", "hist_avg_return"]],
        width="stretch", hide_index=True,
    )
else:
    st.caption("No history yet — run a scan.")
