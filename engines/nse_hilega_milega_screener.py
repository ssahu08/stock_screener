import sys
import os
import time
import datetime
from datetime import date
import webbrowser
import duckdb
from fyers_apiv3 import fyersModel
import numpy as np
import pandas as pd
import requests
import io
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# CONFIGURATION - Keys, settings, paths, and logs
# ============================================================
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.keys import CLIENT_ID, SECRET_KEY, REDIRECT_URI, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from config.settings import UNIVERSE_SETTINGS, STRATEGY_SETTINGS

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "data", "screener.duckdb")

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ============================================================
# 1. TOTAL MARKET TICKER INGESTION
# ============================================================
def fetch_all_nse_stocks():
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        print("\n⏳ Downloading the TOTAL MARKET master list from NSE...")
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        df = pd.read_csv(io.StringIO(response.text))
        df.columns = df.columns.str.strip() 
        df = df[df['SERIES'] == 'EQ']
        
        tickers = ["NSE:" + str(sym).strip() + "-EQ" for sym in df['SYMBOL']]
        print(f"✅ Successfully loaded {len(tickers)} pure equity stocks from NSE.")
        return tickers
    except Exception as e:
        print(f"❌ Failed to download NSE Master List: {e}")
        return ["NSE:RELIANCE-EQ", "NSE:HDFCBANK-EQ", "NSE:INFY-EQ"]

# ============================================================
# 2. PRE-SCAN UNIVERSE FILTERING
# ============================================================
def filter_universe_by_price(fyers_instance, symbols_list, min_price=50, max_price=2000):
    print(f"\n⏳ Pre-filtering {len(symbols_list)} stocks (Price: ₹{min_price} - ₹{max_price})...")
    filtered_symbols = []
    batch_size = 50 
    
    for i in range(0, len(symbols_list), batch_size):
        batch = symbols_list[i:i + batch_size]
        symbols_string = ",".join(batch)
        
        try:
            time.sleep(0.3)  # Rate-limit buffer
            response = fyers_instance.quotes({"symbols": symbols_string})
            if response.get("s") == "ok" and "d" in response:
                for data_point in response["d"]:
                    symbol = data_point["n"]
                    ltp = data_point["v"]["lp"] 
                    if min_price <= ltp <= max_price:
                        filtered_symbols.append(symbol)
        except Exception:
            continue

    print(f"✅ Filter complete! Reduced universe to {len(filtered_symbols)} viable stocks.")
    return filtered_symbols

# ============================================================
# 3. AUTHENTICATION & TELEGRAM
# ============================================================
def authenticate_fyers():
    token_path = os.path.join(BASE_DIR, "data", "fyers_token.txt")
    
    if os.path.exists(token_path):
        file_date = date.fromtimestamp(os.path.getmtime(token_path))
        if file_date == date.today():
            print("✅ Found valid cached token for today. Skipping manual login.")
            with open(token_path, "r") as f:
                return f.read().strip()
                
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
        
    access_token = response["access_token"]
    with open(token_path, "w") as f:
        f.write(access_token)
    print("💾 Token saved successfully for today's session.")
    
    return access_token

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
# 4. STRATEGY MATHEMATICS & REGISTRY PATTERN
# ============================================================
def compute_hilega_milega(df: pd.DataFrame):
    delta = df["Close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=9).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=9).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))
    
    weights = np.arange(1, 22)
    df["WMA"] = df["RSI"].rolling(21).apply(lambda p: np.dot(p, weights) / weights.sum(), raw=True)
    df["EMA"] = df["WMA"].ewm(span=3, adjust=False).mean()

def compute_macd_bb(df: pd.DataFrame):
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    df["MACD"] = exp1 - exp2
    df["Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    
    sma = df['Close'].rolling(window=20).mean()
    std = df['Close'].rolling(window=20).std()
    df["UpperBB"] = sma + (2 * std)
    df["LowerBB"] = sma - (2 * std)

def compute_ma_convergence(df: pd.DataFrame):
    df["MA9"] = df["Close"].rolling(window=9).mean()
    df["MA21"] = df["Close"].rolling(window=21).mean()
    df["MA50"] = df["Close"].rolling(window=50).mean()
    df["MA100"] = df["Close"].rolling(window=100).mean()
    df["MA200"] = df["Close"].rolling(window=200).mean()

# PLUG-AND-PLAY STRATEGY REGISTRY
STRATEGY_REGISTRY = {
    "1": {
        "name": "Hilega Milega",
        "compute_func": compute_hilega_milega,
        "buy_condition": lambda df: (df["RSI"] > df["WMA"]) & (df["EMA"] > df["WMA"]) & (df["RSI"] > STRATEGY_SETTINGS["hilega_milega_rsi_min"]),
        "sell_condition": lambda df: (df["RSI"] < df["WMA"]) & (df["EMA"] < df["WMA"]),
        "metrics_formatter": lambda df: f"RSI: {round(df.iloc[-1]['RSI'],1)} | WMA: {round(df.iloc[-1]['WMA'],1)}",
        "sort_metric": lambda df: df.iloc[-1]["RSI"]
    },
    "2": {
        "name": "MACD + Bollinger Breakout",
        "compute_func": compute_macd_bb,
        "buy_condition": lambda df: (df["MACD"] > df["Signal"]) & (df["Close"] > df["UpperBB"]),
        "sell_condition": lambda df: (df["MACD"] < df["Signal"]) & (df["Close"] < df["LowerBB"]),
        "metrics_formatter": lambda df: f"MACD: {round(df.iloc[-1]['MACD'],2)} | Upper BB: {round(df.iloc[-1]['UpperBB'],2)}",
        "sort_metric": lambda df: df.iloc[-1]["MACD"]
    },
    "3": {
        "name": "MA Convergence (9,21,50,100,200)",
        "compute_func": compute_ma_convergence,
        "buy_condition": lambda df: (df["Close"] > df["MA200"]) & (df["MA9"] > df["MA21"]) & (df["MA21"] > df["MA50"]),
        "sell_condition": lambda df: (df["Close"] < df["MA200"]) & (df["MA9"] < df["MA21"]),
        "metrics_formatter": lambda df: f"MA9: ₹{round(df.iloc[-1]['MA9'],1)} | MA200: ₹{round(df.iloc[-1]['MA200'],1)}",
        "sort_metric": lambda df: df.iloc[-1]["Close"]
    }
}

def backtest_strategy(df: pd.DataFrame, choice: str, forward_periods: int = 4):
    bt_df = df.copy()
    strat_config = STRATEGY_REGISTRY.get(choice)
    
    if not strat_config:
        return 0.0, 0, ""
        
    strat_config["compute_func"](bt_df)
    buy_condition = strat_config["buy_condition"](bt_df)
        
    bt_df["Future_Close"] = bt_df["Close"].shift(-forward_periods)
    bt_df["Forward_Return_Pct"] = ((bt_df["Future_Close"] - bt_df["Close"]) / bt_df["Close"]) * 100
    
    signal_rows = bt_df[buy_condition][:-1].dropna(subset=["Future_Close"])
    
    if signal_rows.empty:
        return 0.0, 0, ""
        
    avg_return = round(signal_rows["Forward_Return_Pct"].mean(), 2)
    total_signals = len(signal_rows)
    signal_dates = signal_rows['Timestamp'].dt.strftime('%Y-%m-%d').tolist()
    
    return avg_return, total_signals, ", ".join(signal_dates)

# ============================================================
# 5. UNIFIED DUCKDB STORAGE
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
            weekly_change_pct DOUBLE,
            hist_signal_count INTEGER,
            hist_avg_return DOUBLE,
            hist_signal_dates VARCHAR
        )
    """)
    con.close()

def save_and_prune_duckdb(results_df: pd.DataFrame, strat_name: str):
    con = duckdb.connect(DB_PATH)
    con.register("new_results", results_df)
    con.execute("INSERT INTO multi_strategy_scans SELECT * FROM new_results")

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
# 6. EXECUTION ENGINE (Multithreaded)
# ============================================================
def execute_scan(fyers, choice, tradable_universe):
    init_db()
    
    strat_config = STRATEGY_REGISTRY.get(choice)
    if not strat_config:
        print("❌ Invalid strategy choice.")
        return

    strat_name = strat_config["name"]
    scan_id = datetime.datetime.now().strftime(f"{strat_name.replace(' ', '').upper()}_%Y%m%d_%H%M%S")
    scan_time = datetime.datetime.now()
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")

    records = []
    buy_signals = []

    print(f"\n🚀 Running {strat_name} Scan on {len(tradable_universe)} filtered stocks concurrently...")
    print(f"⏱️  Expected completion time: ~1 minute.")
    print("-" * 50)

    min_required_bars = 200 if choice == "3" else UNIVERSE_SETTINGS.get("min_history_days", 30)

    def process_ticker(ticker):
        payload = {"symbol": ticker, "resolution": "1D", "date_format": "1", "range_from": from_date, "range_to": today}
        try:
            time.sleep(0.15)  # Safe buffer for threads
            resp = fyers.history(data=payload)

            if not isinstance(resp, dict) or resp.get("s") != "ok" or len(resp.get("candles", [])) < min_required_bars:
                return None

            df = pd.DataFrame(resp["candles"], columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
            df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='s')
            df = df.set_index('Timestamp').resample('W').agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna().reset_index()

            if len(df) < (40 if choice == "3" else 10):
                return None

            latest = df.iloc[-1]
            prev = df.iloc[-2]
            clean_ticker = ticker.replace("NSE:", "").replace("-EQ", "")
            pct_change = round(((latest["Close"] - prev["Close"]) / prev["Close"]) * 100, 2)

            strat_config["compute_func"](df)
            
            is_buy = strat_config["buy_condition"](df).iloc[-1]
            is_sell = strat_config["sell_condition"](df).iloc[-1]

            if is_buy:
                sig = "BUY"
            elif is_sell:
                sig = "SELL"
            else:
                sig = "HOLD"

            metrics_str = strat_config["metrics_formatter"](df)
            sort_metric = strat_config["sort_metric"](df)

            avg_ret, sig_count, sig_dates = backtest_strategy(df, choice, forward_periods=4)

            row = {
                "scan_id": scan_id,
                "scan_time": scan_time,
                "strategy_name": strat_name,
                "ticker": clean_ticker,
                "current_price": round(float(latest["Close"]), 2),
                "signal": sig,
                "key_metrics": metrics_str,
                "weekly_change_pct": pct_change,
                "hist_signal_count": sig_count,
                "hist_avg_return": avg_ret,
                "hist_signal_dates": sig_dates
            }
            
            if sig == "BUY":
                print(f"🟢 BUY DETECTED: {clean_ticker} (₹{round(latest['Close'], 2)}) | Hist 4W Ret: {avg_ret}%")
                
            return {"row": row, "sort_val": sort_metric, "is_buy": sig == "BUY"}
        except Exception:
            return None

    completed_count = 0
    total_stocks = len(tradable_universe)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(process_ticker, ticker): ticker for ticker in tradable_universe}
        
        for future in as_completed(futures):
            completed_count += 1
            if completed_count % 50 == 0 or completed_count == total_stocks:
                print(f"⏳ Progress: [{completed_count}/{total_stocks}] scanned...")
                
            res = future.result()
            if res:
                records.append(res["row"])
                if res["is_buy"]:
                    buy_signals.append({"row": res["row"], "sort_val": res["sort_val"]})

    if records:
        scan_df = pd.DataFrame([r for r in records])
        save_and_prune_duckdb(scan_df, strat_name)
        print("-" * 50)
        print(f"💾 Saved {len(scan_df)} {strat_name} evaluations to DuckDB.")

        msg = f"📊 *{strat_name.upper()} TOTAL MARKET SCAN*\n"
        msg += f"🗓 *Scan Time:* `{scan_time.strftime('%Y-%m-%d %H:%M')}`\n"
        msg += f"──────────────────────\n"

        if buy_signals:
            buy_signals = sorted(buy_signals, key=lambda x: x['sort_val'], reverse=True)
            top_buys = [b["row"] for b in buy_signals[:15]]
            
            msg += f"🚀 *TOP {len(top_buys)} BUY SETUPS (Price: ₹{UNIVERSE_SETTINGS['min_price']} - ₹{UNIVERSE_SETTINGS['max_price']}):*\n\n"
            for s in top_buys:
                msg += (f"• *{s['ticker']}* | ₹{s['current_price']}\n"
                        f"  {s['key_metrics']} | Change: `{s['weekly_change_pct']}%`\n"
                        f"  Backtest (4W): `{s['hist_signal_count']} signals` | Avg Ret: `{s['hist_avg_return']}%`\n"
                        f"  Dates: `{s['hist_signal_dates']}`\n\n")
        else:
            msg += "⚪ *No BUY signals detected.*\n"

        send_telegram_alert(msg)
        print("📲 Telegram summary dispatched.")

# ============================================================
# 7. MAIN MENU LOOP
# ============================================================
if __name__ == "__main__":
    print("Initializing Fyers Connection...")
    token = authenticate_fyers()
    fyers_instance = fyersModel.FyersModel(client_id=CLIENT_ID, is_async=False, token=token, log_path=LOG_DIR)
    
    raw_tickers = fetch_all_nse_stocks()
    filtered_universe = filter_universe_by_price(
        fyers_instance, 
        raw_tickers, 
        min_price=UNIVERSE_SETTINGS["min_price"], 
        max_price=UNIVERSE_SETTINGS["max_price"]
    )
    
    while True:
        print("\n" + "="*50)
        print("📈 INSTITUTIONAL ALGO SCREENER MENU")
        print("="*50)
        
        for key, config in STRATEGY_REGISTRY.items():
            print(f"{key}. Strategy: {config['name']}")
        print(f"{len(STRATEGY_REGISTRY) + 1}. Exit")
        
        choice = input(f"\nSelect an option (1-{len(STRATEGY_REGISTRY) + 1}): ").strip()
        
        exit_option = str(len(STRATEGY_REGISTRY) + 1)
        if choice == exit_option:
            print("Exiting engine. Goodbye!")
            break
        elif choice in STRATEGY_REGISTRY:
            execute_scan(fyers_instance, choice, filtered_universe)
        else:
            print(f"❌ Invalid selection. Please type a number between 1 and {exit_option}.")