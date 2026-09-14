import datetime
import time
import webbrowser
import duckdb
from fyers_apiv3 import fyersModel
import numpy as np
import pandas as pd
import requests
import io

# ============================================================
# CONFIGURATION - Keys and data
# ============================================================

import sys
import os

# Add the parent directory so Python can find the 'config' folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.keys import CLIENT_ID, SECRET_KEY, REDIRECT_URI, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# Point to the new database location dynamically
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "screener.duckdb")

# ============================================================
# 1. TOTAL MARKET TICKER INGESTION
# ============================================================
def fetch_all_nse_stocks():
    # The official NSE master list of all listed securities
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        print("\n⏳ Downloading the TOTAL MARKET master list from NSE...")
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Read the CSV directly from memory
        df = pd.read_csv(io.StringIO(response.text))
        
        # CRITICAL FILTER: Keep only regular Equities ('EQ' series)
        df.columns = df.columns.str.strip() 
        df = df[df['SERIES'] == 'EQ']
        
        # Format the NSE symbols specifically for the Fyers API
        tickers = ["NSE:" + str(sym).strip() + "-EQ" for sym in df['SYMBOL']]
        print(f"✅ Successfully loaded {len(tickers)} pure equity stocks from NSE.")
        return tickers
        
    except Exception as e:
        print(f"❌ Failed to download NSE Master List: {e}")
        # Fallback watchlist if NSE servers are down
        return ["NSE:RELIANCE-EQ", "NSE:HDFCBANK-EQ", "NSE:INFY-EQ"]

TICKERS = fetch_all_nse_stocks()

# ============================================================
# 2. AUTHENTICATION & TELEGRAM
# ============================================================
def authenticate_fyers():
    session = fyersModel.SessionModel(
        client_id=CLIENT_ID,
        secret_key=SECRET_KEY,
        redirect_uri=REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code"
    )
    auth_url = session.generate_authcode()
    print("\n🌐 Opening browser for Fyers login...")
    webbrowser.open(auth_url, new=1)
    auth_code = input("Paste the 'auth_code' from the browser URL here: ").strip()

    session.set_token(auth_code)
    response = session.generate_token()
    
    if "access_token" not in response:
        print("❌ Login Failed. Fyers response:", response)
        exit()
    return response["access_token"]

def send_telegram_alert(message: str):
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"\n❌ Telegram API Error: {response.text}")
    except Exception as err:
        print(f"Failed to send Telegram alert: {err}")

# ============================================================
# 3. STRATEGY MATHEMATICS
# ============================================================
def compute_hilega_milega(df: pd.DataFrame):
    delta = df["Close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=9).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=9).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    weights = np.arange(1, 22)
    wma = rsi.rolling(21).apply(lambda p: np.dot(p, weights) / weights.sum(), raw=True)
    ema = wma.ewm(span=3, adjust=False).mean()
    return rsi, wma, ema

def compute_macd_bb(df: pd.DataFrame):
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=9, adjust=False).mean()
    
    sma = df['Close'].rolling(window=20).mean()
    std = df['Close'].rolling(window=20).std()
    upper_bb = sma + (2 * std)
    lower_bb = sma - (2 * std)
    return macd, signal_line, upper_bb, lower_bb

# ============================================================
# 4. UNIFIED DUCKDB STORAGE
# ============================================================
def init_db():
    con = duckdb.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS multi_strategy_scans (
            scan_id VARCHAR,
            scan_time TIMESTAMP,
            strategy_name VARCHAR,
            ticker VARCHAR,
            current_price DOUBLE,
            signal VARCHAR,
            key_metrics VARCHAR,
            weekly_change_pct DOUBLE
        )
    """)
    con.close()

def save_and_prune_duckdb(results_df: pd.DataFrame, strat_name: str):
    con = duckdb.connect(DB_PATH)
    con.register("new_results", results_df)
    con.execute("INSERT INTO multi_strategy_scans SELECT * FROM new_results")

    # Retain only the last 2 scans FOR THIS SPECIFIC STRATEGY
    distinct_scans = con.execute(f"""
        SELECT DISTINCT scan_id FROM multi_strategy_scans 
        WHERE strategy_name = '{strat_name}'
        ORDER BY scan_id DESC LIMIT 2
    """).fetchall()

    valid_scan_ids = [row[0] for row in distinct_scans]
    if valid_scan_ids:
        placeholders = ",".join(["?"] * len(valid_scan_ids))
        con.execute(f"DELETE FROM multi_strategy_scans WHERE strategy_name = '{strat_name}' AND scan_id NOT IN ({placeholders})", valid_scan_ids)
    con.close()

# ============================================================
# 5. EXECUTION ENGINE
# ============================================================
def execute_scan(fyers, choice):
    init_db()
    
    if choice == "1":
        strat_name = "Hilega Milega"
    elif choice == "2":
        strat_name = "MACD + Bollinger Breakout"

    scan_id = datetime.datetime.now().strftime(f"{strat_name.replace(' ', '').upper()}_%Y%m%d_%H%M%S")
    scan_time = datetime.datetime.now()
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")

    records = []
    buy_signals = []

    print(f"\n🚀 Running {strat_name} Scan on entire NSE Market...")
    print(f"⏱️  Expected completion time: 5 to 7 minutes. Please leave this window open.")
    print("-" * 50)

    for ticker in TICKERS:
        payload = {"symbol": ticker, "resolution": "1D", "date_format": "1", "range_from": from_date, "range_to": today}
        try:
            time.sleep(0.15) # Safety buffer for 10 requests/sec limit
            resp = fyers.history(data=payload)

            if resp.get("s") != "ok" or len(resp.get("candles", [])) < 30:
                continue

            df = pd.DataFrame(resp["candles"], columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
            df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='s')
            df = df.set_index('Timestamp').resample('W').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna().reset_index()

            latest = df.iloc[-1]
            prev = df.iloc[-2]
            clean_ticker = ticker.replace("NSE:", "").replace("-EQ", "")
            pct_change = round(((latest["Close"] - prev["Close"]) / prev["Close"]) * 100, 2)

            # STRATEGY ROUTING LOGIC
            if choice == "1":
                df["RSI"], df["WMA"], df["EMA"] = compute_hilega_milega(df)
                l_rsi, l_wma, l_ema = df.iloc[-1]["RSI"], df.iloc[-1]["WMA"], df.iloc[-1]["EMA"]
                
                if l_rsi > l_wma and l_ema > l_wma and l_rsi > 50:
                    sig = "BUY"
                elif l_rsi < l_wma and l_ema < l_wma:
                    sig = "SELL"
                else:
                    sig = "HOLD"
                
                metrics_str = f"RSI: {round(l_rsi,1)} | WMA: {round(l_wma,1)}"
                sort_metric = l_rsi

            elif choice == "2":
                df["MACD"], df["Signal"], df["UpperBB"], df["LowerBB"] = compute_macd_bb(df)
                l_macd, l_sig, l_upper = df.iloc[-1]["MACD"], df.iloc[-1]["Signal"], df.iloc[-1]["UpperBB"]
                l_close = latest["Close"]
                
                if l_macd > l_sig and l_close > l_upper:
                    sig = "BUY"
                elif l_macd < l_sig and l_close < df.iloc[-1]["LowerBB"]:
                    sig = "SELL"
                else:
                    sig = "HOLD"
                    
                metrics_str = f"MACD: {round(l_macd,2)} | Upper BB: {round(l_upper,2)}"
                sort_metric = l_macd

            # Save the record
            row = {
                "scan_id": scan_id,
                "scan_time": scan_time,
                "strategy_name": strat_name,
                "ticker": clean_ticker,
                "current_price": round(float(latest["Close"]), 2),
                "signal": sig,
                "key_metrics": metrics_str,
                "weekly_change_pct": pct_change,
            }
            records.append(row)

            if sig == "BUY":
                # Filter out penny stocks (Price < 50) for BUY signals
                if latest["Close"] >= 50.0:
                    buy_signals.append({"row": row, "sort_val": sort_metric})
                    print(f"🟢 BUY DETECTED: {clean_ticker} (₹{round(latest['Close'], 2)})")
                else:
                    print(f"🟡 Ignored {clean_ticker}: Penny stock (₹{round(latest['Close'], 2)})")

        except Exception:
            continue

    if records:
        scan_df = pd.DataFrame(records)
        save_and_prune_duckdb(scan_df, strat_name)
        print("-" * 50)
        print(f"💾 Saved {len(scan_df)} {strat_name} evaluations to DuckDB.")

        msg = f"📊 *{strat_name.upper()} TOTAL MARKET SCAN*\n"
        msg += f"🗓 *Scan Time:* `{scan_time.strftime('%Y-%m-%d %H:%M')}`\n"
        msg += f"──────────────────────\n"

        if buy_signals:
            # Sort by the most relevant metric (RSI or MACD strength)
            buy_signals = sorted(buy_signals, key=lambda x: x['sort_val'], reverse=True)
            top_buys = [b["row"] for b in buy_signals[:15]]
            
            msg += f"🚀 *TOP {len(top_buys)} BUY SETUPS (Price > ₹50):*\n\n"
            for s in top_buys:
                msg += (f"• *{s['ticker']}* | ₹{s['current_price']}\n"
                        f"  {s['key_metrics']} | Change: `{s['weekly_change_pct']}%`\n")
        else:
            msg += "⚪ *No BUY signals detected.*\n"

        send_telegram_alert(msg)
        print("📲 Telegram summary dispatched.")

# ============================================================
# 6. MAIN MENU LOOP
# ============================================================
if __name__ == "__main__":
    print("Initializing Fyers Connection...")
    token = authenticate_fyers()
    fyers_instance = fyersModel.FyersModel(client_id=CLIENT_ID, is_async=False, token=token, log_path="")
    
    while True:
        print("\n" + "="*50)
        print("📈 INSTITUTIONAL ALGO SCREENER MENU")
        print("="*50)
        print("1. Strategy: Hilega Milega (Momentum)")
        print("2. Strategy: MACD + Bollinger Bands (Breakout)")
        print("3. Exit")
        
        choice = input("\nSelect an option (1/2/3): ").strip()
        
        if choice == "3":
            print("Exiting engine. Goodbye!")
            break
        elif choice in ["1", "2"]:
            execute_scan(fyers_instance, choice)
        else:
            print("❌ Invalid selection. Please type 1, 2, or 3.")