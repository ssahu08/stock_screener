import requests
import pandas as pd
import numpy as np
import datetime

# Replace with your actual Alpha Vantage API Key
API_KEY = "MBGWZLR7UT0DCLWW"

# Keep the list small (under 25) to avoid hitting the daily free API limit!
# Alpha Vantage uses .BSE for Indian stocks
TICKERS = ["RELIANCE.BSE", "TCS.BSE", "INFY.BSE"]

def calculate_wma(data, period):
    weights = np.arange(1, period + 1)
    return data.rolling(period).apply(lambda prices: np.dot(prices, weights) / weights.sum(), raw=True)

def hilega_milega(data):
    # RSI 9
    delta = data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=9).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=9).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    
    # WMA 21 of RSI
    wma = calculate_wma(rsi, 21)
    
    # EMA 3 of WMA
    ema = wma.ewm(span=3, adjust=False).mean()
    
    return rsi, wma, ema

def run_screener():
    print("=" * 60)
    print(f"Screening {len(TICKERS)} stocks via ALPHA VANTAGE")
    print("Timeframe: WEEKLY | Indicator: Hilega Milega")
    print("=" * 60)
    
    results = []
    
    for ticker in TICKERS:
        print(f"Screening {ticker}...", end=" ")
        try:
            # Fetch Weekly Data from Alpha Vantage
            url = f"https://www.alphavantage.co/query?function=TIME_SERIES_WEEKLY&symbol={ticker}&apikey={API_KEY}"
            response = requests.get(url)
            data = response.json()
            
            # Check for API limits or missing data
            if "Information" in data:
                print("❌ API Limit Reached (25 calls/day on free tier)")
                break
            if "Weekly Time Series" not in data:
                print("❌ Skipped (Ticker not found or data error)")
                continue
                
            # Convert JSON to Pandas DataFrame
            ts = data["Weekly Time Series"]
            stock = pd.DataFrame.from_dict(ts, orient='index')
            stock = stock.rename(columns={"4. close": "Close"})
            stock['Close'] = stock['Close'].astype(float)
            
            # Sort dates from oldest to newest for accurate rolling math
            stock.index = pd.to_datetime(stock.index)
            stock = stock.sort_index(ascending=True)
            
            # Need at least ~30 weeks to calculate the 21-period WMA accurately
            if len(stock) < 30:
                print("❌ Skipped (Insufficient weekly data)")
                continue
                
            stock['RSI'], stock['WMA'], stock['EMA'] = hilega_milega(stock)
            
            latest = stock.iloc[-1]
            prev = stock.iloc[-2]
            
            if pd.isna(latest['RSI']) or pd.isna(latest['WMA']) or pd.isna(latest['EMA']):
                print("❌ Skipped (Not enough weeks to calculate)")
                continue
                
            # The Hilega Milega Logic Rules
            if latest['RSI'] > latest['WMA'] and latest['EMA'] > latest['WMA'] and latest['RSI'] > 50:
                signal = "BUY"
                print("✓ BUY")
            elif latest['RSI'] < latest['WMA'] and latest['EMA'] < latest['WMA']:
                signal = "SELL"
                print("✓ SELL")
            else:
                signal = "HOLD"
                print("✓ HOLD")
                
            pct_change = ((latest['Close'] - prev['Close']) / prev['Close']) * 100
            
            results.append({
                'ticker': ticker,
                'current_price': round(float(latest['Close']), 2),
                'weekly_signal': signal,
                'rsi': round(float(latest['RSI']), 1),
                'wma': round(float(latest['WMA']), 1),
                'ema': round(float(latest['EMA']), 1),
                'weekly_change_pct': round(float(pct_change), 2)
            })
            
        except Exception as e:
            print(f"❌ Skipped (Error: {e})")
            
    print("\n" + "=" * 60)
    print("ALPHA VANTAGE RESULTS")
    print("=" * 60)
    
    if not results:
        print("No results to display.")
        return
        
    df = pd.DataFrame(results)
    print(df.to_string(index=False))
        
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_file = f"alpha_vantage_screening_{timestamp}.csv"
    df.to_csv(csv_file, index=False)
    print(f"\n💾 Results saved as: {csv_file}")

if __name__ == "__main__":
    run_screener()