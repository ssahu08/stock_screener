# config/settings.py

# Universal Constraints
UNIVERSE_SETTINGS = {
    "min_price": 50.0,
    "max_price": 2000.0,
    "min_history_days": 365,   # Fyers caps 1D range at 366 days; ~52 weekly bars
    "min_weekly_bars": 22,     # WMA(21) needs 22 weekly bars minimum
    "max_workers": 4,          # parallel data-fetch threads
    "top_n_display": 15,       # top N BUY signals in console/Telegram
    "forward_weeks": 4,        # forward-return horizon for backtest stats
}

# Strategy Specific Thresholds
STRATEGY_SETTINGS = {
    "hilega_milega_rsi_min": 50.0,
}
