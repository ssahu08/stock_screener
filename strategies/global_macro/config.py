# Global Macro Config
# ===================
# ETF universe, thresholds, capital allocation

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class ETFConfig:
    symbol: str           # NSE symbol (e.g., "MON100")
    index: str            # US index name (must match fetcher)
    name: str             # Display name
    lot_size: int = 1     # ETFs typically 1 unit


# Your ETF Universe
ETF_UNIVERSE = [
    ETFConfig(
        symbol="MON100",
        index="Nasdaq 100",
        name="Motilal Oswal Nasdaq 100 ETF",
        lot_size=1,
    ),
    ETFConfig(
        symbol="MONQ50",
        index="Nasdaq 100",
        name="Motilal Oswal Nasdaq Q50 ETF",
        lot_size=1,
    ),
    ETFConfig(
        symbol="MASPTOP50",
        index="S&P 500",
        name="Mirae Asset S&P 500 Top 50 ETF",
        lot_size=1,
    ),
    ETFConfig(
        symbol="MAFANG",
        index="S&P 500",
        name="Mirae Asset NYSE FANG+ ETF",
        lot_size=1,
    ),
]

# Index -> ETFs mapping
INDEX_TO_ETFS = {
    "Nasdaq 100": ["MON100", "MONQ50"],
    "S&P 500": ["MASPTOP50", "MAFANG"],
    "Dow Jones": [],
    "Russell 2000": [],
}

# Thresholds: +/- 1% for both directions
THRESHOLDS = {
    "Nasdaq 100": 1.0,
    "S&P 500": 1.0,
    "Dow Jones": 1.0,
    "Russell 2000": 1.0,
}

MAJOR_MOVE_THRESHOLD = 2.0  # Alert if any index moves >2%

# Capital allocation
CAPITAL_PER_TRADE = 10000  # Rs. 10,000 per trade


# Fyers symbol format
def nse_symbol(etf_symbol: str) -> str:
    return f"NSE:{etf_symbol}-EQ"