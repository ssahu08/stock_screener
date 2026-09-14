import os
import streamlit as st
import duckdb
import pandas as pd
import yfinance as yf

# ============================================================
# PAGE CONFIGURATION
# ============================================================
st.set_page_config(page_title="Institutional Algo Screener", page_icon="📈", layout="wide")

# Resolving path relative to this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "screener.duckdb")

# ============================================================
# DATA LOADING & CACHING
# ============================================================
@st.cache_data(ttl=10)
def load_technical_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    try:
        con = duckdb.connect(DB_PATH)
        df = con.execute("SELECT * FROM multi_strategy_scans ORDER BY scan_time DESC").df()
        con.close()
        return df
    except duckdb.CatalogException:
        return pd.DataFrame()

@st.cache_data(show_spinner="Screening fundamentals via Yahoo Finance... Please wait.")
def run_fundamental_screen(scan_id: str, strategy_type: str, buy_records: list):
    """
    Evaluates BUY candidates against selected fundamental criteria.
    Results are cached by scan_id and strategy_type to prevent duplicate Yahoo Finance queries.
    """
    passed_stocks = []

    for row in buy_records:
        ticker = row['ticker']
        price = row['current_price']

        try:
            stock = yf.Ticker(f"{ticker}.NS")
            info = stock.info

            # Financial metrics extraction
            roe = info.get('returnOnEquity', 0) or 0
            debt_raw = info.get('debtToEquity', 0) or 0
            debt_eq = debt_raw / 100.0
            pe_ratio = info.get('trailingPE', 0) or 999.0
            pb_ratio = info.get('priceToBook', 0) or 999.0
            qtr_earnings_growth = info.get('earningsQuarterlyGrowth', 0) or 0.0
            high_52w = info.get('fiftyTwoWeekHigh', 0) or price

            passed = False
            reason = ""

            # Strategy 1: Quality Compounders
            if strategy_type == "Quality Compounders":
                if roe >= 0.15 and debt_eq <= 0.5:
                    passed = True
                    reason = f"ROE: {round(roe * 100, 1)}% | D/E: {round(debt_eq, 2)}"

            # Strategy 2: PEAD Momentum
            elif strategy_type == "PEAD Momentum":
                # Quarterly earnings growth >= 20% & within 15% of 52-week high
                if qtr_earnings_growth >= 0.20 and price >= (high_52w * 0.85):
                    pct_from_high = round(((price - high_52w) / high_52w) * 100, 1)
                    passed = True
                    reason = f"Qtr EPS Growth: +{round(qtr_earnings_growth * 100, 1)}% | {pct_from_high}% from 52W High"

            # Strategy 3: Deep Value
            elif strategy_type == "Deep Value":
                if 0 < pe_ratio < 15 and 0 < pb_ratio < 2:
                    passed = True
                    reason = f"P/E: {round(pe_ratio, 1)} | P/B: {round(pb_ratio, 1)}"

            if passed:
                passed_stocks.append({
                    "Ticker": ticker,
                    "Current Price (₹)": price,
                    "Fundamental Highlights": reason,
                    "Technical Setup": row['key_metrics'],
                    "ROE (%)": round(roe * 100, 2) if roe else "N/A",
                    "Debt/Equity": round(debt_eq, 2),
                    "P/E Ratio": round(pe_ratio, 2) if pe_ratio < 999 else "N/A",
                    "52W High (₹)": round(high_52w, 2)
                })

        except Exception:
            continue

    return pd.DataFrame(passed_stocks)

# ============================================================
# MAIN UI
# ============================================================
st.title("📈 Institutional Algo Screener Dashboard")

df = load_technical_data()

if df.empty:
    st.warning("⚠️ No scan records found in `data/screener.duckdb`. Run a scan using `screener_engine.py` first.")
    st.stop()

# --- SIDEBAR FILTERS ---
st.sidebar.header("⚙️ Technical Scan Controls")

strategies = df['strategy_name'].unique().tolist()
selected_strategy = st.sidebar.selectbox("1. Technical Strategy", strategies)
strat_df = df[df['strategy_name'] == selected_strategy]

scan_ids = strat_df['scan_id'].unique().tolist()
selected_scan = st.sidebar.selectbox("2. Scan Timestamp", scan_ids)
run_df = strat_df[strat_df['scan_id'] == selected_scan]

signals = ["ALL"] + sorted(run_df['signal'].unique().tolist())
selected_signal = st.sidebar.selectbox("3. Signal Filter", signals)
filtered_tech_df = run_df if selected_signal == "ALL" else run_df[run_df['signal'] == selected_signal]

# --- METRIC CARDS ---
total_scanned = len(run_df)
total_buys = len(run_df[run_df['signal'] == 'BUY'])
total_sells = len(run_df[run_df['signal'] == 'SELL'])
scan_time_str = run_df['scan_time'].iloc[0].strftime('%Y-%m-%d %H:%M') if not run_df.empty else "N/A"

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Scanned", total_scanned)
col2.metric("🟢 BUY Signals", total_buys)
col3.metric("🔴 SELL Signals", total_sells)
col4.metric("⏱️ Execution Time", scan_time_str)

st.divider()

# --- TAB INTERFACE ---
tab1, tab2 = st.tabs(["📊 Technical Results", "🏆 Techno-Fundamental Elite"])

# ------------------------------------------------------------
# TAB 1: TECHNICAL SCREENER
# ------------------------------------------------------------
with tab1:
    st.subheader(f"Technical Results: {selected_strategy}")

    if filtered_tech_df.empty:
        st.info("No tickers match the selected signal filter.")
    else:
        display_df = filtered_tech_df[['ticker', 'signal', 'current_price', 'key_metrics', 'weekly_change_pct']].copy()
        display_df.columns = ['Ticker', 'Signal', 'Price (₹)', 'Key Indicators', 'Weekly Change (%)']

        def color_signal(val):
            if val == 'BUY':
                return 'color: #00FF00; font-weight: bold'
            elif val == 'SELL':
                return 'color: #FF0000; font-weight: bold'
            return 'color: #888888'

        styled_df = display_df.style.map(color_signal, subset=['Signal'])
        st.dataframe(styled_df, use_container_width=True, hide_index=True, height=520)

# ------------------------------------------------------------
# TAB 2: TECHNO-FUNDAMENTAL SCREENER
# ------------------------------------------------------------
with tab2:
    st.subheader("Techno-Fundamental Filter")
    st.caption("Validates technical BUY setups against balance sheet and income statement fundamentals.")

    buys_only_df = run_df[run_df['signal'] == 'BUY']

    if buys_only_df.empty:
        st.info("There are no technical BUY setups in this scan to screen fundamentally.")
    else:
        col_strat, col_btn = st.columns([3, 1])

        with col_strat:
            selected_fund_strategy = st.selectbox(
                "Select Fundamental Verification Strategy:",
                [
                    "Quality Compounders",
                    "PEAD Momentum",
                    "Deep Value"
                ],
                help=(
                    "• Quality Compounders: ROE >= 15%, Debt/Equity <= 0.5\n"
                    "• PEAD Momentum: Quarterly Earnings Growth >= 20% and Price within 15% of 52W High\n"
                    "• Deep Value: P/E < 15 and Price-to-Book < 2.0"
                )
            )

        with col_btn:
            st.write("") # Spacer to align vertically with selectbox
            st.write("")
            run_button = st.button("🔎 Run Fundamental Screen", use_container_width=True)

        # Show criteria description box
        if selected_fund_strategy == "Quality Compounders":
            st.info("**Strategy Benchmark:** ROE ≥ 15% and Debt-to-Equity ≤ 0.5 (Conservative leverage, high reinvestment efficiency).")
        elif selected_fund_strategy == "PEAD Momentum":
            st.info("**Strategy Benchmark:** YoY Quarterly EPS Growth ≥ +20% and trading within 15% of 52-Week High (Exploiting analyst under-reaction).")
        elif selected_fund_strategy == "Deep Value":
            st.info("**Strategy Benchmark:** Trailing P/E < 15 and Price-to-Book < 2.0 (High margin of safety).")

        # Execute scan on click or if already cached
        if run_button:
            buy_records = buys_only_df[['ticker', 'current_price', 'key_metrics']].to_dict('records')
            elite_df = run_fundamental_screen(selected_scan, selected_fund_strategy, buy_records)

            if elite_df.empty:
                st.warning(f"No technical BUY setups qualified under the **{selected_fund_strategy}** rules.")
            else:
                st.success(f"🎯 **{len(elite_df)}** of {len(buys_only_df)} BUY setups passed the {selected_fund_strategy} screen!")
                st.dataframe(elite_df, use_container_width=True, hide_index=True)