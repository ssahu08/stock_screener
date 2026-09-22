#!/usr/bin/env python3
"""
Hilega Milega Screener - Test Script
Fetches real Fyers data and shows BUY signals for verification
"""

import sys
import os
import time
import datetime
from datetime import date
import webbrowser
import pandas as pd
import numpy as np
import requests
import io

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fyers_apiv3 import fyersModel
from config.keys import CLIENT_ID, SECRET_KEY, REDIRECT_URI

# ============================================================
# Fyers Authentication
# ============================================================
def authenticate_fyers():
    token_path = os.path.join(os.path.dirname(__file__), "data", "fyers_token.txt")
    
    # Check if token exists and is from today
    if os.path.exists(token_path):
        file_date = date.fromtimestamp(os.path.getmtime(token_path))
        if file_date == date.today():
            print("✅ Found valid cached token for today")
            with open(token_path, "r") as f:
                return f.read().strip()
    
    # Generate new auth URL
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
    
    auth_code = input("\n📋 Paste the 'auth_code' from the browser URL here: ").strip()
    session.set_token(auth_code)
    response = session.generate_token()
    
    if "access_token" not in response:
        print("❌ Login Failed. Response:", response)
        sys.exit(1)
    
    access_token = response["access_token"]
    
    # Save token for today
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w") as f:
        f.write(access_token)
    print("💾 Token saved for today's session")
    
    return access_token

# ============================================================
# Fetch NSE Stock List
# ============================================================
def fetch_nse_stocks():
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    try:
        print("\n⏳ Downloading NSE stock list...")
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        df = pd.read_csv(io.StringIO(response.text))
        df.columns = df.columns.str.strip()
        df = df[df['SERIES'] == 'EQ']
        
        tickers = ["NSE:" + str(sym).strip() + "-EQ" for sym in df['SYMBOL']]
        print(f"✅ Loaded {len(tickers)} NSE stocks")
        return tickers
    except Exception as e:
        print(f"❌ Failed to fetch NSE list: {e}")
        # Return sample for testing
        return [
            "NSE:RELIANCE-EQ", "NSE:TCS-EQ", "NSE:HDFCBANK-EQ",
            "NSE:INFY-EQ", "NSE:ICICIBANK-EQ", "NSE:HINDUNILVR-EQ",
            "NSE:ITC-EQ", "NSE:SBIN-EQ", "NSE:BHARTIARTL-EQ",
            "NSE:KOTAKBANK-EQ"
        ]

# ============================================================
# Filter by Price
# ============================================================
def filter_by_price(fyers, tickers, min_price=50, max_price=2000):
    print(f"\n⏳ Filtering stocks (₹{min_price}-₹{max_price})...")
    filtered = []
    batch_size = 50
    
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        symbols = ",".join(batch)
        
        try:
            time.sleep(0.3)
            response = fyers.quotes({"symbols": symbols})
            if response.get("s") == "ok" and "d" in response:
                for item in response["d"]:
                    ltp = item["v"]["lp"]
                    if min_price <= ltp <= max_price:
                        filtered.append(item["n"])
        except:
            continue
    
    print(f"✅ Filtered to {len(filtered)} stocks")
    return filtered

# ============================================================
# Hilega Milega Indicator Calculations
# ============================================================
def compute_hilega_milega(df):
    """
    Calculate Hilega Milega indicators:
    - RSI(9): 9-period RSI
    - WMA: 21-period Weighted MA of RSI (the "red line" - most important)
    - EMA: 3-period EMA of WMA (the "green line")
    """
    # RSI 9-period
    delta = df["Close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=9).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=9).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))
    
    # WMA 21 of RSI (weights 1-21, more weight to recent)
    weights = np.arange(1, 22)
    df["WMA"] = df["RSI"].rolling(21).apply(
        lambda p: np.dot(p, weights) / weights.sum(), raw=True
    )
    
    # EMA 3 of WMA
    df["EMA"] = df["WMA"].ewm(span=3, adjust=False).mean()
    
    return df

# ============================================================
# Buy Signal Logic
# ============================================================
def check_buy_signal(df):
    """
    BUY Signal Conditions:
    1. RSI > 50 (bullish territory)
    2. WMA > 50 (trend intact in strength zone)
    3. Current close > Previous week's high (candle confirmation)
    """
    if len(df) < 22:  # Need at least 21 bars for WMA + 1 for shift
        return False, {}
    
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    rsi = latest["RSI"]
    wma = latest["WMA"]
    ema = latest["EMA"]
    close = latest["Close"]
    prev_high = prev["High"]
    
    # Check conditions
    cond_rsi_bullish = rsi > 50
    cond_wma_in_zone = wma > 50
    cond_candle_confirm = close > prev_high
    
    is_buy = cond_rsi_bullish and cond_wma_in_zone and cond_candle_confirm
    
    metrics = {
        "RSI": round(rsi, 1),
        "WMA": round(wma, 1),
        "EMA": round(ema, 1),
        "Close": round(close, 2),
        "Prev_High": round(prev_high, 2),
        "RSI>50": "✓" if cond_rsi_bullish else "✗",
        "WMA>50": "✓" if cond_wma_in_zone else "✗",
        "Close>PrevHigh": "✓" if cond_candle_confirm else "✗"
    }
    
    return is_buy, metrics

# ============================================================
# Main Screener
# ============================================================
def run_screener():
    print("=" * 70)
    print("🚀 HILEGA MILEGA SCREENER - Test Mode")
    print("=" * 70)
    
    # Authenticate
    token = authenticate_fyers()
    fyers = fyersModel.FyersModel(
        client_id=CLIENT_ID,
        is_async=False,
        token=token,
        log_path=os.path.join(os.path.dirname(__file__), "logs")
    )
    
    # Fetch and filter stocks
    all_stocks = fetch_nse_stocks()
    filtered_stocks = filter_by_price(fyers, all_stocks, min_price=50, max_price=2000)
    
    if not filtered_stocks:
        print("❌ No stocks after filtering. Using sample list.")
        filtered_stocks = [
            "NSE:RELIANCE-EQ", "NSE:TCS-EQ", "NSE:HDFCBANK-EQ",
            "NSE:INFY-EQ", "NSE:ICICIBANK-EQ"
        ]
    
    print(f"\n🚀 Running Hilega Milega scan on {len(filtered_stocks)} stocks...")
    print("-" * 70)
    
    buy_signals = []
    all_results = []
    
    # Scan each stock
    for i, ticker in enumerate(filtered_stocks, 1):
        if i % 50 == 0:
            print(f"⏳ Progress: {i}/{len(filtered_stocks)} scanned...")
        
        try:
            # Fetch 1 year of daily data
            payload = {
                "symbol": ticker,
                "resolution": "1D",
                "date_format": "1",
                "range_from": (datetime.datetime.now() - datetime.timedelta(days=365)).strftime("%Y-%m-%d"),
                "range_to": datetime.datetime.now().strftime("%Y-%m-%d")
            }
            
            time.sleep(0.15)
            resp = fyers.history(data=payload)
            
            if not isinstance(resp, dict) or resp.get("s") != "ok" or len(resp.get("candles", [])) < 30:
                continue
            
            # Convert to DataFrame
            df = pd.DataFrame(resp["candles"], columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
            df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='s')
            
            # Resample to weekly
            df = df.set_index('Timestamp').resample('W').agg({
                'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
            }).dropna().reset_index()
            
            # Need at least 22 weeks for WMA calculation
            if len(df) < 22:
                continue
            
            # Calculate indicators
            df = compute_hilega_milega(df)
            
            # Check BUY signal
            is_buy, metrics = check_buy_signal(df)
            
            clean_ticker = ticker.replace("NSE:", "").replace("-EQ", "")
            latest_close = round(float(df.iloc[-1]["Close"]), 2)
            
            result = {
                "ticker": clean_ticker,
                "price": latest_close,
                "signal": "BUY" if is_buy else "HOLD",
                **metrics
            }
            all_results.append(result)
            
            if is_buy:
                buy_signals.append(result)
                print(f"🟢 BUY: {clean_ticker} @ ₹{latest_close} | RSI={metrics['RSI']} WMA={metrics['WMA']}")
                
        except Exception as e:
            continue
    
    # Display Results
    print("\n" + "=" * 70)
    print(f"📊 HILEGA MILEGA SCAN RESULTS")
    print("=" * 70)
    
    if buy_signals:
        print(f"\n✅ Found {len(buy_signals)} BUY signals:\n")
        
        for i, signal in enumerate(buy_signals, 1):
            print(f"{i}. {signal['ticker']} @ ₹{signal['price']}")
            print(f"   RSI: {signal['RSI']} | WMA: {signal['WMA']} | EMA: {signal['EMA']}")
            print(f"   Conditions: RSI>50={signal['RSI>50']} WMA>50={signal['WMA>50']} Close>PrevHigh={signal['Close>PrevHigh']}")
            print()
    else:
        print("\n⚪ No BUY signals detected in this scan.")
    
    # Save to CSV for review
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if buy_signals:
        csv_file = f"buy_signals_{timestamp}.csv"
        pd.DataFrame(buy_signals).to_csv(csv_file, index=False)
        print(f"\n💾 BUY signals saved to: {csv_file}")
    
    # Summary stats
    total_scanned = len(all_results)
    total_buy = len(buy_signals)
    print(f"\n📈 Summary: {total_buy}/{total_scanned} stocks triggered BUY signals")
    
    return buy_signals

# ============================================================
if __name__ == "__main__":
    signals = run_screener()
    
    if signals:
        print("\n" + "=" * 70)
        print("📋 PLEASE VERIFY THESE SIGNALS:")
        print("=" * 70)
        print("Open each stock in TradingView with these settings:")
        print("  1. RSI: Length=9, Levels=50/50")
        print("  2. Add WMA: Length=21 on RSI")
        print("  3. Add EMA: Length=3 on WMA")
        print("\nCheck if RSI>50, WMA>50, and this week's close > last week's high")
        print("=" * 70)
