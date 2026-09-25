# Global Macro Data Sources
# =========================
# Multi-source index data fetching with fallback chain

import os
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from abc import ABC, abstractmethod


class IndexDataSource(ABC):
    """Abstract base for index data sources."""
    
    @abstractmethod
    def get_previous_close_pct(self, indices: List[str]) -> Dict[str, Optional[float]]:
        """Return {index_name: pct_change} for previous trading day."""
        pass
    
    @abstractmethod
    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current/latest price for intraday alerts."""
        pass


class FinnhubSource(IndexDataSource):
    """Finnhub.io - Free tier: 60 calls/min, no credit card needed."""
    
    BASE_URL = "https://finnhub.io/api/v1"
    
    def __init__(self, api_key: str = None):
        # Fall back to project secrets (.streamlit/secrets.toml) so Finnhub
        # also works on Streamlit Cloud where no env vars are set.
        if not api_key:
            api_key = os.getenv("FINNHUB_API_KEY")
        if not api_key:
            try:
                from config.keys import get_secret
                api_key = get_secret("FINNHUB_TOKEN")
            except Exception:
                api_key = ""
        self.api_key = api_key
        if not self.api_key:
            raise ValueError("FINNHUB_API_KEY required")
    
    def get_previous_close_pct(self, indices: List[str]) -> Dict[str, Optional[float]]:
        finnhub_map = {
            "S&P 500": "^GSPC",
            "Nasdaq 100": "^NDX",
            "Dow Jones": "^DJI",
            "Russell 2000": "^RUT",
        }
        
        results = {}
        for name in indices:
            symbol = finnhub_map.get(name)
            if not symbol:
                results[name] = None
                continue
            
            try:
                url = f"{self.BASE_URL}/quote"
                params = {"symbol": symbol, "token": self.api_key}
                resp = requests.get(url, params=params, timeout=10)
                data = resp.json()
                
                current = data.get("c")
                prev_close = data.get("pc")
                
                if current and prev_close and prev_close > 0:
                    pct = (current - prev_close) / prev_close * 100
                    results[name] = round(pct, 2)
                else:
                    results[name] = None
                    
            except Exception:
                results[name] = None
        
        return results
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        try:
            url = f"{self.BASE_URL}/quote"
            params = {"symbol": symbol, "token": self.api_key}
            resp = requests.get(url, params=params, timeout=5)
            return resp.json().get("c")
        except Exception:
            return None


class TwelveDataSource(IndexDataSource):
    """Twelve Data - Backup source (800 calls/day free)."""
    
    BASE_URL = "https://api.twelvedata.com"
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("TWELVE_DATA_API_KEY")
        if not self.api_key:
            raise ValueError("TWELVE_DATA_API_KEY required")
    
    def get_previous_close_pct(self, indices: List[str]) -> Dict[str, Optional[float]]:
        td_map = {
            "S&P 500": "SPX",
            "Nasdaq 100": "NDX",
            "Dow Jones": "DJI",
            "Russell 2000": "RUT",
        }
        
        results = {}
        for name in indices:
            symbol = td_map.get(name)
            if not symbol:
                results[name] = None
                continue
            
            try:
                url = f"{self.BASE_URL}/time_series"
                params = {
                    "symbol": symbol,
                    "interval": "1day",
                    "outputsize": 2,
                    "apikey": self.api_key,
                }
                resp = requests.get(f"{self.BASE_URL}/time_series", params=params, timeout=10)
                data = resp.json()
                
                values = data.get("values", [])
                if len(values) >= 2:
                    prev = float(values[1]["close"])
                    curr = float(values[0]["close"])
                    pct = (curr - prev) / prev * 100
                    results[name] = round(pct, 2)
                else:
                    results[name] = None
                    
            except Exception:
                results[name] = None
        
        return results
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        try:
            url = f"{self.BASE_URL}/price"
            params = {"symbol": symbol, "apikey": self.api_key}
            resp = requests.get(url, params=params, timeout=5)
            return float(resp.json().get("price", 0))
        except Exception:
            return None


class YFinanceSource(IndexDataSource):
    """Fallback - yfinance (no API key needed)."""
    
    def get_previous_close_pct(self, indices: List[str]) -> Dict[str, Optional[float]]:
        yf_map = {
            "S&P 500": "^GSPC",
            "Nasdaq 100": "^NDX",
            "Dow Jones": "^DJI",
            "Russell 2000": "^RUT",
        }
        
        results = {}
        end = datetime.now()
        start = datetime.now() - timedelta(days=5)
        
        for name in indices:
            symbol = yf_map.get(name)
            if not symbol:
                results[name] = None
                continue
            
            try:
                # Use auto_adjust=False to get raw OHLCV
                df = yf.download(symbol, start=start, end=datetime.now(), progress=False, auto_adjust=False)
                if df.empty or len(df) < 2:
                    results[name] = None
                    continue
                
                # Handle MultiIndex columns from yfinance (new format: ticker as first level)
                if isinstance(df.columns, pd.MultiIndex):
                    # Find the Close column for this symbol
                    close_cols = [c for c in df.columns if c[0] == 'Close' and c[1] == symbol]
                    if not close_cols:
                        # Try without ^ prefix
                        close_cols = [c for c in df.columns if c[0] == 'Close' and c[1] == symbol.replace('^', '')]
                    if close_cols:
                        close_series = df[close_cols[0]]
                    else:
                        results[name] = None
                        continue
                else:
                    if 'Close' in df.columns:
                        close_series = df['Close']
                    else:
                        results[name] = None
                        continue
                
                if len(close_series) >= 2:
                    prev = close_series.iloc[-2]
                    curr = close_series.iloc[-1]
                    pct = (curr - prev) / prev * 100
                    results[name] = round(float(pct), 2)
                else:
                    results[name] = None
            except Exception:
                results[name] = None
        
        return results
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        try:
            ticker = yf.Ticker(symbol)
            return ticker.fast_info.get("last_price")
        except Exception:
            return None


class MultiSourceIndexFetcher:
    """Try sources in order: Finnhub -> Twelve Data -> yfinance."""
    
    def __init__(self):
        self.sources = []
        
        for source_class in [FinnhubSource, TwelveDataSource, YFinanceSource]:
            try:
                self.sources.append(source_class())
                print(f"Initialized {source_class.__name__}")
            except Exception as e:
                print(f"{source_class.__name__} unavailable: {e}")
        
        if not self.sources:
            raise RuntimeError("No data sources available!")
    
    def get_previous_close_pct(self, indices: List[str]) -> Dict[str, Optional[float]]:
        for source in self.sources:
            try:
                result = source.get_previous_close_pct(indices)
                if all(v is not None for v in result.values()):
                    return result
            except Exception:
                continue
        return self.sources[-1].get_previous_close_pct(indices)
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        for source in self.sources:
            try:
                price = source.get_current_price(symbol)
                if price:
                    return price
            except Exception:
                continue
        return None