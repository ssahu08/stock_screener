# NSE Stock Screener

A Python-based stock screener for Indian equities (NSE) that identifies
stocks exhibiting specific technical setups. Built with a pluggable
**strategy registry** so new setups (Hilega Milega, Volcano Squeeze, ...)
can be added without touching the engine.

## Current strategies

| Key | Name | Bars | Role |
|-----|------|------|------|
| `hilega_milega` | Hilega Milega (RSI-9 momentum) | weekly | trend-momentum (in 2-week live trial) |
| `volcano_watch` | Volcano Watch | daily | **advance watchlist** — squeeze forming BEFORE the breakout (fast BB(10) windows) |
| `volcano_blast` | Volcano Blast | daily | breakout trigger — squeeze → close > upper band + 1.5× volume |
| `volcano_snap` | Volcano Snap | daily | fast trigger — BB(10) squeeze + 1.2× volume, sized for 4–6-session coils |
| `coil_break` | Coil Break | daily | Bollinger-inside-Keltner squeeze → same expansion trigger |
| `darvas_box` | Darvas Box | daily | flat 20-bar box → close above the box high |
| `volcano_blast_60` | Volcano Blast 60 | 60 min | Blast rules on hourly bars (Fyers caps intraday history at 100 days) |

### The squeeze test bench

The family from `volcano_watch` down is a **test bench** reverse-engineered
from your own chart screenshots (`dataset/*.JPG`): Bollinger compression
("squeeze") → expansion-candle breakout. Every variant shares ONE exit
rule (close falls below the band mid after a recent squeeze) so that
ENTRY rules stay directly comparable while you review which category
fits your style. They can be scanned together — data is fetched once
per stock and shared:

```bash
python engine.py -s volcano_watch,volcano_blast,volcano_snap -u nifty50
```

## Architecture

```
stock_screener/
├── config/
│   ├── keys.py              # Fyers + Telegram credentials (gitignored)
│   └── settings.py          # Universe filters, thresholds, workers
├── core/
│   ├── auth.py              # Option A login (run by YOU, token stays local)
│   ├── data.py              # NSE universe + Fyers OHLC fetching
│   └── alerts.py            # Telegram notifications
├── strategies/
│   ├── base.py              # Strategy ABC — subclass to add a strategy
│   ├── registry.py          # Register strategies here (appears in menu/UI)
│   ├── hilega_milega.py     # Hilega Milega implementation
│   └── volcano.py           # Squeeze family: Watch/Blast/Snap/Coil/Darvas/60
├── storage/
│   └── db.py                # DuckDB persistence + schema migration
├── engine.py                # Scan orchestration (CLI + library)
├── app.py                   # Streamlit dashboard (Run button, history, stats)
├── login shortcut: core/auth.py
└── data/screener.duckdb     # results database (gitignored)
```

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Authenticate with Fyers (run this YOURSELF — Option A)
#    Browser opens -> log in -> paste the auth_code when asked
python core/auth.py

# 3a. Run from the dashboard (recommended)
streamlit run app.py

# 3b. Or run from the terminal
python engine.py                                  # interactive menu
python engine.py -s hilega_milega -u nifty50      # quick sample
python engine.py -s hilega_milega                 # full NSE scan
python engine.py -s hilega_milega --no-alert      # skip Telegram
python engine.py -s hilega_milega,volcano_watch,volcano_snap  # bench run
```

Tokens expire daily (~6:30 AM IST). Re-run `python core/auth.py`
when the dashboard shows the 🔐 message.

## Hilega Milega — implemented rules

**Indicators (weekly bars):**
- RSI length **9**, overbought/oversold both at **50** (momentum line)
- **WMA 21** of RSI — the red line (key line)
- **EMA 3** of WMA — the green line

**BUY:** RSI > 50 AND WMA > 50 AND close > previous week's high
**SELL:** RSI < 50 AND WMA < 50 AND close < previous week's low

**Extras surfaced per signal:**
- Historical signal count + avg forward 4-week return (backtest)
- Zone-breakout stop level: low (longs) / high (shorts) of the candle
  where the red line first left the zone
- Profit-taking warning when EMA touches RSI (within 1 point)

> ⚠️ The ~70–75% accuracy claim is the original trader's own estimate —
> unverified. Treat signals as candidates for review, not trading advice.

## Adding a new strategy

1. Create `strategies/my_strategy.py`:

```python
from strategies.base import Strategy

class MyStrategy(Strategy):
    @property
    def name(self): return "My Strategy"
    @property
    def key(self):  return "my_strategy"

    def compute(self, df):          # add indicator columns
        ...
    def buy_signal(self, df):       # boolean Series (all rows)
        ...
    def sell_signal(self, df):      # boolean Series (all rows)
        ...
    def format_metrics(self, df):   # display string
        ...
    def sort_value(self, df):       # rank key for BUY signals
        ...
```

2. Register it in `strategies/registry.py`:

```python
from strategies.my_strategy import MyStrategy

@register_strategy
class _Registered(MyStrategy):
    pass
```

It now appears in the CLI menu and the Streamlit sidebar automatically.

## Data & storage

- **Data source:** Fyers API v3 (requires an active Fyers subscription)
- **Universe:** NSE EQUITY_L master list, filtered SERIES=EQ
- **Price filter:** ₹50–₹2,000 (configurable in sidebar / settings.py)
- **Storage:** DuckDB (`data/screener.duckdb`), keeps the 2 most recent
  scans per strategy; older scans are pruned automatically
- **Alerts:** Telegram summary of top BUY setups (configured in keys.py)

## Security notes (Option A)

- The Fyers login is **run by you** in your own terminal — tokens never
  pass through chat transcripts or shared environments.
- `config/keys.py`, `data/`, `logs/`, and `*.csv` are gitignored.
- Fyers access tokens expire daily; the app only checks for today's token.
