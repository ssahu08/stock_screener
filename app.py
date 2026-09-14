import streamlit as st
import duckdb
import pandas as pd
import os

# ==========================================
# 1. PAGE SETUP
# ==========================================
st.set_page_config(page_title="Stock Screener", layout="wide", page_icon="📈")
st.title("Stock Screener")
st.markdown("Monitor high-probability swing trading setups based on historical 4-week forward returns.")

# ==========================================
# 2. DATABASE CONNECTION
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "screener.duckdb")

@st.cache_data(ttl=30) 
def load_data():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
        
    con = duckdb.connect(DB_PATH, read_only=True)
    query = """
        SELECT scan_time, strategy_name, ticker, current_price, signal, 
               weekly_change_pct, key_metrics, hist_signal_count, hist_avg_return, hist_signal_dates 
        FROM multi_strategy_scans
        ORDER BY hist_avg_return DESC
    """
    try:
        df = con.execute(query).df()
    except Exception:
        df = pd.DataFrame()
    finally:
        con.close()
    return df

df = load_data()

# ==========================================
# 3. DASHBOARD UI
# ==========================================
if df.empty:
    st.warning("⚠️ No scan data found. Please run your Python engine first.")
else:
    strategies = df['strategy_name'].unique().tolist()
    selected_strategy = st.selectbox("🎯 Select Strategy", strategies)
    
    display_df = df[(df['strategy_name'] == selected_strategy) & (df['signal'] == "BUY")]
    
    if not display_df.empty:
        latest_time = display_df['scan_time'].max().strftime('%Y-%m-%d %H:%M')
        st.caption(f"Last Updated: {latest_time} | Showing filtered stocks (₹50 - ₹2000)")

        # Render the dataframe with constrained column configurations for a cleaner look
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "scan_time": None, 
                "strategy_name": None, 
                "signal": None, 
                "ticker": st.column_config.TextColumn("Ticker", width="small"),
                "current_price": st.column_config.NumberColumn("Price", format="₹%.2f", width="small"),
                "weekly_change_pct": st.column_config.NumberColumn("1W Change", format="%.2f%%", width="small"),
                "key_metrics": st.column_config.TextColumn("Indicator Strength", width="medium"),
                "hist_signal_count": st.column_config.NumberColumn("Signals", format="%d", width="small"),
                "hist_avg_return": st.column_config.NumberColumn("Hist. 4W Return", format="%.2f%%", width="small"),
                "hist_signal_dates": st.column_config.TextColumn("Past Signal Dates", width="large", help="Dates when this setup previously triggered.")
            }
        )
    else:
        st.info(f"No BUY signals detected for {selected_strategy} in the latest scan.")