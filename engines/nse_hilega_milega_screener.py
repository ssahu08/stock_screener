import yfinance as yf
import pandas as pd
import numpy as np
import datetime

# Broader Indian Market Tickers (Nifty Next 50 & Midcap)
TICKERS = [
    "HAL.NS", "BEL.NS", "ZOMATO.NS", "TRENT.NS", "TVSMOTOR.NS", 
    "JINDALSTEL.NS", "CUMMINSIND.NS", "DIXON.NS", "BHEL.NS", "LODHA.NS",
    "PFC.NS", "RECLTD.NS", "IRFC.NS", "RVNL.NS", "MAZDOCK.NS",
    "CHOLAFIN.NS", "SHRIRAMFIN.NS", "TORNTPHARM.NS", "LUPIN.NS", "AUROPHARMA.NS",
    "BOSCHLTD.NS", "MRF.NS", "PIIND.NS", "POLYCAB.NS", "HAVELLS.NS",
    "CGPOWER.NS", "SUZLON.NS", "KALYANKJIL.NS", "KPITTECH.NS", "COFORGE.NS",
    "PERSISTENT.NS", "OFSS.NS", "TATAELXSI.NS", "APOLLOTYRE.NS", "ESCORTS.NS",
    "ASHOKLEY.NS", "GMRINFRA.NS", "DLF.NS", "GODREJPROP.NS", "OBEROIRLTY.NS",
    "PRESTIGE.NS", "INDHOTEL.NS", "TATACHEM.NS", "DEEPAKNTR.NS", "NAUKRI.NS",
    "PAYTM.NS", "POLICYBZR.NS", "NYKAA.NS", "MAXHEALTH.NS", "ABCAPITAL.NS"
]

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
    print(f"Screening {len(TICKERS)} BROADER MARKET stocks")
    print("Timeframe: WEEKLY | Indicator: Hilega Milega")
    print("=" * 60)
    
    results = []
    
    for ticker in TICKERS:
        print(f"Screening {ticker}...", end=" ")
        try:
            # CHANGED: 2 years of history, Weekly interval
            stock = yf.download(ticker, period="2y", interval="1wk", progress=False)
            
            if stock.empty or len(stock) < 30:
                print("❌ Skipped (Insufficient weekly data)")
                continue
                
            if isinstance(stock.columns, pd.MultiIndex):
                stock.columns = stock.columns.get_level_values(0)
                
            stock['RSI'], stock['WMA'], stock['EMA'] = hilega_milega(stock)
            
            # Using the last closed week
            latest = stock.iloc[-1]
            prev = stock.iloc[-2]
            
            if pd.isna(latest['RSI']) or pd.isna(latest['WMA']) or pd.isna(latest['EMA']):
                print("❌ Skipped (Not enough weeks to calculate)")
                continue
                
            # Logic: BUY if RSI > WMA and EMA > WMA (Upward Momentum)
            if latest['RSI'] > latest['WMA'] and latest['EMA'] > latest['WMA']:
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
            print("❌ Skipped (Data calculation error)")
            
    print("\n" + "=" * 60)
    print("WEEKLY SCREENING RESULTS")
    print("=" * 60)
    
    if not results:
        print("No results to display.")
        return
        
    df = pd.DataFrame(results)
    
    buy_signals = df[df['weekly_signal'] == 'BUY']
    if not buy_signals.empty:
        print("\n📈 WEEKLY BUY SIGNALS (Strong Structural Momentum):")
        print(buy_signals.to_string(index=False))
    else:
        print("\nNo Weekly BUY signals found. The broader market may be correcting structurally.")
        
    # Save to CSV
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_file = f"weekly_broad_market_screening_{timestamp}.csv"
    df.to_csv(csv_file, index=False)
    print(f"\n💾 Full weekly results saved to your folder as: {csv_file}")

if __name__ == "__main__":
    run_screener()