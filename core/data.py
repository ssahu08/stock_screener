"""
Data Module
===========

Handles NSE stock universe loading and Fyers data fetching.
All API calls use the authenticated token from core/auth.py.

⚠️ IMPORTANT: 
- Token must be valid (see core/auth.py for Option A login)
- Fyers API rate limits apply (~3-5 calls/sec safe zone)
- All timestamps are IST (India Standard Time) unless noted
"""

import sys
import os
import time
import datetime
import io
import requests
import pandas as pd
import numpy as np
from typing import Optional

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fyers_apiv3 import fyersModel
from core.auth import get_token


# ============================================================
# NSE Stock Universe
# ============================================================
def fetch_nse_universe() -> list:
    """Download the complete NSE equity master list.
    
    Returns:
        list of ticker strings like ["NSE:RELIANCE-EQ", "NSE:TCS-EQ", ...]
    """
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    try:
        print("⏳ Downloading NSE master list...")
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        df = pd.read_csv(io.StringIO(response.text))
        df.columns = df.columns.str.strip()
        df = df[df['SERIES'] == 'EQ']
        
        tickers = [f"NSE:{str(sym).strip()}-EQ" for sym in df['SYMBOL']]
        print(f"✅ Loaded {len(tickers)} pure equity stocks from NSE")
        return tickers
    except Exception as e:
        print(f"❌ Failed to download NSE list: {e}")
        # Return commonly traded stocks as fallback
        return [
            "NSE:RELIANCE-EQ", "NSE:TCS-EQ", "NSE:HDFCBANK-EQ",
            "NSE:INFY-EQ", "NSE:ICICIBANK-EQ", "NSE:HINDUNILVR-EQ",
            "NSE:ITC-EQ", "NSE:SBIN-EQ", "NSE:BHARTIARTL-EQ",
            "NSE:KOTAKBANK-EQ"
        ]


def filter_by_price(fyers: fyersModel.FyersModel,
                    symbols: list,
                    min_price: float = 50.0,
                    max_price: float = 2000.0) -> list:
    """Filter NSE stocks by LTP price range.
    
    Args:
        fyers: Authenticated FyersModel instance
        symbols: List of ticker strings
        min_price: Minimum LTP (₹)
        max_price: Maximum LTP (₹)
    
    Returns:
        Filtered list of symbols within price range
    """
    print(f"\n⏳ Pre-filtering {len(symbols)} stocks (₹{min_price}-₹{max_price})...")
    filtered = []
    batch_size = 50
    
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        symbols_str = ",".join(batch)
        
        try:
            time.sleep(0.3)  # Rate-limit buffer
            response = fyers.quotes({"symbols": symbols_str})
            
            if response.get("s") == "ok" and "d" in response:
                for data_point in response["d"]:
                    symbol = data_point["n"]
                    ltp = data_point["v"]["lp"]
                    if min_price <= ltp <= max_price:
                        filtered.append(symbol)
        except Exception as e:
            # Log but continue - one batch failure shouldn't stop scan
            pass
    
    print(f"✅ Reduced universe to {len(filtered)} viable stocks")
    return filtered


# ============================================================
# Historical Data Fetching
# ============================================================
def fetch_historical_data(fyers: fyersModel.FyersModel,
                         symbol: str,
                         days: int = 365,
                         resolution: str = "1D") -> Optional[pd.DataFrame]:
    """Fetch historical candle data for a single symbol.

    Args:
        fyers: Authenticated FyersModel instance
        symbol: Ticker string like "NSE:RELIANCE-EQ"
        days: Number of trading days back (default: 365 ≈ 1 year)
              NOTE: Fyers caps 1D/1W/1M ranges at 366 days.
        resolution: "1D" for daily, "1W" for weekly, etc.

    Returns:
        DataFrame with columns: Timestamp, Open, High, Low, Close, Volume
            (empty DataFrame = request OK but not enough candles)
        None = API error / network failure (RETRYABLE — caller should retry)
    """
    today = datetime.date.today()
    from_date = today - datetime.timedelta(days=days)

    payload = {
        "symbol": symbol,
        "resolution": resolution,
        "date_format": "1",
        "range_from": from_date.strftime("%Y-%m-%d"),
        "range_to": today.strftime("%Y-%m-%d")
    }

    try:
        time.sleep(0.15)  # Safe buffer
        resp = fyers.history(data=payload)

        if not isinstance(resp, dict) or resp.get("s") != "ok":
            return None  # retryable API failure

        candles = resp.get("candles", [])
        if not candles or len(candles) < 22:
            return pd.DataFrame()  # valid response, too little history

        # Convert to DataFrame
        df = pd.DataFrame(candles, columns=[
            "Timestamp", "Open", "High", "Low", "Close", "Volume"
        ])

        # Convert timestamps
        df['Timestamp'] = pd.to_datetime(df['Timestamp'], unit='s')
        df = df.set_index('Timestamp')

        # Sort oldest to newest (important for rolling calculations)
        df = df.sort_index(ascending=True)
        return df

    except Exception:
        return None  # network/parse failure — retryable


def resample_to_weekly(df_daily: pd.DataFrame) -> pd.DataFrame:
    """Resample daily data to weekly OHLCV.
    
    Weekly aggregation:
    - Open: first value of the week
    - High: maximum of the week
    - Low: minimum of the week
    - Close: last value of the week
    - Volume: sum of the week
    
    Args:
        df_daily: DataFrame with daily candles (must be sorted ascending)
    
    Returns:
        Weekly OHLCV DataFrame ready for Hilega Milega indicators
    """
    if df_daily.empty:
        return pd.DataFrame()
    
    df_weekly = df_daily.resample('W').agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna().reset_index()
    
    # Rename index column back to 'Timestamp' for consistency
    if 'Timestamp' in df_weekly.columns:
        df_weekly = df_weekly.rename(columns={'Timestamp': 'Timestamp'})
    
    return df_weekly


# ============================================================
# Quotes / LTP Data
# ============================================================
def get_ltp(fyers: fyersModel.FyersModel, symbols: list) -> dict:
    """Get Last Traded Price for a list of symbols.
    
    Args:
        fyers: Authenticated FyersModel instance
        symbols: List of ticker strings
    
    Returns:
        Dict mapping symbol -> { "n": symbol, "v": {"lp": LTP, ...} }
    """
    batch_size = 50
    results = {}
    
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        symbols_str = ",".join(batch)
        
        try:
            time.sleep(0.3)
            response = fyers.quotes({"symbols": symbols_str})
            
            if response.get("s") == "ok" and "d" in response:
                for data_point in response["d"]:
                    symbol = data_point["n"]
                    ltp = data_point["v"]["lp"]
                    results[symbol] = {
                        "n": symbol,
                        "v": {"lp": ltp}
                    }
        except Exception:
            continue
    
    return results


# ============================================================
# Export utilities
# ============================================================
def get_nifty50_symbols() -> list:
    """Return Nifty 50 tickers (NSE symbol format) for quick testing."""
    symbols = [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
        "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
        "LT", "ASIANPAINT", "MARUTI", "TATASTEEL", "ONGC",
        "SUNPHARMA", "ULTRACEMCO", "NTPC", "COALINDIA", "POWERGRID",
        "BPCL", "DIVISLAB", "NMDC", "JSWSTEEL", "BAJFINANCE",
        "HDFCLIFE", "EICHERMOT", "TITAN", "AXISBANK", "WIPRO",
        "TECHM", "ADANIENT", "ADANIPORTS", "TATAMOTORS", "TATACONSUM",
        "INDUSINDBK", "GRASIM", "HCLTECH", "CIPLA", "DRREDDY",
        "APOLLOHOSP", "BAJAJFINSV", "BRITANNIA", "HEROMOTOCO",
        "NESTLEIND", "ONGC", "SBILIFE", "SHRIRAMFIN", "TATAPOWER",
    ]
    return [f"NSE:{s}-EQ" for s in symbols]