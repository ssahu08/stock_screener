"""
Strategy Base Class
====================
Every strategy (Hilega Milega, Volcano Squeeze, etc.) inherits from this class.
To add a new strategy:
  1. Create a new file in strategies/
  2. Subclass Strategy and implement the required methods
  3. Register it in strategies/registry.py
"""

from abc import ABC, abstractmethod
import pandas as pd


class Strategy(ABC):
    """Abstract base class that all trading strategies must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable display name. e.g. 'Hilega Milega'"""
        pass

    @property
    @abstractmethod
    def key(self) -> str:
        """Unique machine key. e.g. 'hilega_milega'"""
        pass

    @property
    def min_bars(self) -> int:
        """Minimum number of bars needed for reliable signals.
        Override if your strategy needs more history.
        Default: 22 (enough for a 21-period WMA + 1 confirmation bar)
        """
        return 22

    @property
    def timeframe(self) -> str:
        """Bar size the engine feeds into compute()/buy_signal()/sell_signal():
        "weekly" (default) | "daily" | "60min".

        The engine fetches daily data ONCE per ticker and derives weekly
        frames by resampling; "60min" additionally triggers an intraday
        fetch. Multiple strategies in one scan share these fetches.
        """
        return "weekly"

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add indicator columns to the DataFrame.
        Called once per ticker before signal evaluation.
        Must add whatever columns buy_signal/sell_signal need.
        """
        pass

    @abstractmethod
    def buy_signal(self, df: pd.DataFrame) -> pd.Series:
        """Return a boolean Series: True on rows where BUY conditions are met.
        Typically only the last row matters for live scanning,
        but returning the full Series enables backtesting.
        """
        pass

    @abstractmethod
    def sell_signal(self, df: pd.DataFrame) -> pd.Series:
        """Return a boolean Series: True on rows where SELL conditions are met."""
        pass

    @abstractmethod
    def format_metrics(self, df: pd.DataFrame) -> str:
        """Format the latest row's indicator values for display.
        e.g. 'RSI: 78.5 | WMA: 63.7 | EMA: 62.2'
        """
        pass

    @abstractmethod
    def sort_value(self, df: pd.DataFrame) -> float:
        """Numeric value used to rank BUY signals (higher = stronger signal).
        Used to sort the top-N results.
        """
        pass

    # ------------------------------------------------------------------
    # Helpers (optional overrides)
    # ------------------------------------------------------------------
    def evaluate(self, df: pd.DataFrame) -> dict:
        """Convenience: compute indicators and return latest signal info.
        Used by the engine so it doesn't need to know strategy internals.
        """
        df = self.compute(df)
        latest_buy = bool(self.buy_signal(df).iloc[-1])
        latest_sell = bool(self.sell_signal(df).iloc[-1])

        if latest_buy:
            signal = "BUY"
        elif latest_sell:
            signal = "SELL"
        else:
            signal = "HOLD"

        return {
            "signal": signal,
            "metrics": self.format_metrics(df),
            "sort_value": self.sort_value(df),
            "is_buy": latest_buy,
        }
