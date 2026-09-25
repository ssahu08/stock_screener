# Index Follower Strategy
# =======================
# Global Macro / Overnight Gap Strategy
# US Index -> Indian ETF

import pandas as pd
from datetime import datetime, date, time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from strategies.base import Strategy
from strategies.global_macro.config import (
    ETF_UNIVERSE, INDEX_TO_ETFS, THRESHOLDS, MAJOR_MOVE_THRESHOLD,
    CAPITAL_PER_TRADE, nse_symbol
)
from strategies.global_macro.data_sources import MultiSourceIndexFetcher
from config.keys import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


# -----------------------------------------------------------------------------
# Strategy Class
# -----------------------------------------------------------------------------

class IndexFollower(Strategy):
    """
    Index Follower - Global Macro / Overnight Gap Strategy
    
    Logic:
    - Monitor US indices (S&P 500, Nasdaq 100, Dow, Russell) daily close
    - If index closes >= +1% -> LONG corresponding Indian ETFs next day
    - If index closes <= -1% -> SHORT corresponding Indian ETFs next day
    - Entry: Next day 9:15 AM market order
    - Exit: User discretion
    - Capital: Rs. 10,000 per trade
    - Both LONG (+1%) and SHORT (-1%) signals
    
    Alerts:
    - SOD 9:00 AM: US close % + ETFs to trade
    - Intraday: Major move alerts (>2%)
    - EOD 3:45 PM: Summary + P&L
    """
    
    def __init__(self):
        self.fetcher = MultiSourceIndexFetcher()
        self.indices = ["S&P 500", "Nasdaq 100", "Dow Jones", "Russell 2000"]
    
    @property
    def name(self) -> str:
        return "Index Follower (Global Macro)"
    
    @property
    def key(self) -> str:
        return "index_follower"
    
    @property
    def timeframe(self) -> str:
        return "daily"
    
    @property
    def min_bars(self) -> int:
        return 5
    
    @property
    def category(self) -> str:
        return "global_macro"
    
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        return df
    
    def buy_signal(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(False, index=df.index)
    
    def sell_signal(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(False, index=df.index)
    
    def format_metrics(self, df: pd.DataFrame) -> str:
        return "Global Macro Index Follower"
    
    def sort_value(self, df: pd.DataFrame) -> float:
        return 0.0
    
    # -------------------------------------------------------------------------
    # Core Logic
    # -------------------------------------------------------------------------
    
    def fetch_us_indices(self) -> Dict[str, Optional[float]]:
        """Fetch previous day close % for all tracked indices."""
        return self.fetcher.get_previous_close_pct(self.indices)
    
    def generate_signals(self) -> List[Dict]:
        """Generate trade signals for next trading day."""
        us_data = self.fetch_us_indices()
        signals = []
        
        for index_name, pct in us_data.items():
            if pct is None:
                continue
            
            threshold = 1.0  # +/-1%
            etfs = INDEX_TO_ETFS.get(index_name, [])
            
            if not etfs:
                continue
            
            # LONG signal
            if pct >= 1.0:
                for etf_symbol in INDEX_TO_ETFS[index_name]:
                    signals.append({
                        "type": "LONG",
                        "us_index": index_name,
                        "us_pct": pct,
                        "etf_symbol": etf_symbol,
                        "fyers_symbol": f"NSE:{etf_symbol}-EQ",
                        "capital": CAPITAL_PER_TRADE,
                        "direction": "BUY",
                        "trigger": f"{index_name} +{pct:.2f}%",
                    })
            
            # SHORT signal
            elif pct <= -1.0:
                for etf_symbol in INDEX_TO_ETFS[index_name]:
                    signals.append({
                        "type": "SHORT",
                        "us_index": index_name,
                        "us_pct": pct,
                        "etf_symbol": etf_symbol,
                        "fyers_symbol": f"NSE:{etf_symbol}-EQ",
                        "capital": CAPITAL_PER_TRADE,
                        "direction": "SELL",
                        "trigger": f"{index_name} {pct:.2f}%",
                    })
        
        return signals
    
    def check_major_moves(self) -> List[Dict]:
        """Check for major moves (>2%) for intraday alerts."""
        us_data = self.fetch_us_indices()
        alerts = []
        
        for index_name, pct in us_data.items():
            if pct is None:
                continue
            if abs(pct) >= 2.0:
                etfs = INDEX_TO_ETFS.get(index_name, [])
                direction = "SURGE" if pct > 0 else "PLUNGE"
                alerts.append({
                    "index": index_name,
                    "pct": pct,
                    "direction": direction,
                    "etfs": INDEX_TO_ETFS.get(index_name, []),
                    "message": f"{index_name} {direction} {abs(pct):.2f}% -- Watch {', '.join(INDEX_TO_ETFS.get(index_name, []))}",
                })
        
        return alerts
    
    def get_sod_plan(self) -> Dict:
        """Generate Start-of-Day trading plan."""
        us_data = self.fetch_us_indices()
        signals = self.generate_signals()
        major_alerts = self.check_major_moves()
        
        return {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "us_indices": {k: v for k, v in us_data.items() if v is not None},
            "signals": signals,
            "major_alerts": major_alerts,
            "timestamp": datetime.now().isoformat(),
        }
    
    def format_sod_telegram(self, plan: Dict) -> str:
        lines = [
            "INDEX FOLLOWER - SOD PLAN (9:00 AM)",
            f"Date: {plan['date']}",
            "-" * 30,
            "US CLOSE (Prev Day):"
        ]
        
        for idx, pct in plan["us_indices"].items():
            emoji = "GREEN" if pct and pct > 0 else ("RED" if pct and pct < 0 else "GRAY")
            lines.append(f"   {emoji} {idx}: {pct:+.2f}%")
        
        if plan["signals"]:
            lines.append("\nTRADE SIGNALS:")
            for s in plan["signals"]:
                lines.append(f"   {s['direction']} {s['etf_symbol']} -- {s['trigger']} | Rs.{s['capital']:,}")
        
        if plan["major_alerts"]:
            lines.append("\nMAJOR MOVES:")
            for a in plan["major_alerts"]:
                lines.append(f"   {a['message']}")
        
        if not plan["signals"]:
            lines.append("\nNo signals today (threshold not met)")
        
        lines.append("\nCapital: Rs.10,000 per trade | Exit: User discretion")
        return "\n".join(lines)
    
    def format_eod_summary(self, trades_today: List[Dict]) -> str:
        lines = [
            "INDEX FOLLOWER - EOD SUMMARY (3:45 PM)",
            f"Date: {datetime.now().strftime('%Y-%m-%d')}",
            "-" * 30,
        ]
        
        if trades_today:
            lines.append(f"\nTRADES TODAY ({len(trades_today)}):")
            total_pnl = 0
            for t in trades_today:
                pnl = t.get("pnl", 0)
                lines.append(f"   {t['direction']} {t['etf']} | Entry: Rs.{t['entry']:.2f} | Exit: Rs.{t['exit']:.2f} | P&L: Rs.{pnl:+,.0f}")
                total_pnl += pnl
            lines.append(f"\nNet P&L: Rs.{total_pnl:+,.0f}")
        else:
            lines.append("\nNo trades executed today")
        
        lines.append("\nSee you tomorrow at 9:00 AM!")
        return "\n".join(lines)


# -----------------------------------------------------------------------------
# Registry Registration
# -----------------------------------------------------------------------------

def register(registry_mod):
    @registry_mod.register_strategy
    class _BuiltInIndexFollower(IndexFollower):
        def __init__(self):
            super().__init__()