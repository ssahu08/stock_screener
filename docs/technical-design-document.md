# Technical Design Document — NSE Stock Screener

| | |
|---|---|
| **Document** | Technical Design Document (TDD) |
| **Version** | 1.0 |
| **Date** | 26-Sep-2026 |
| **Status** | Draft for review |
| **Codebase** | `github.com/ssahu08/stock_screener` (private) — main @ `bee1340` |
| **Production** | `https://stockscreener2026.streamlit.app` (Streamlit Community Cloud) |
| **Companion docs** | [BRD](brd.md) · [User Guide](user-guide.md) · `CLOUD_SETUP.md` · `README.md` |

### Revision history

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 26-Sep-2026 | Sahu / AI assistant | Initial release: architecture, components, infra, security, roadmap |

---

## 1. Purpose & scope

This document describes the design of the **NSE Stock Screener** — a
mobile-first, multi-category trading research system for Indian equities and
index derivatives. It is written for engineers who will maintain or extend
the system, and for reviewers validating its architecture.

**In scope:** application architecture, tech stack, component design, data
flow, integrations, security model, infrastructure, testing, limitations,
and the delivery roadmap.

**Out of scope:** order placement/execution (the system is research + alerts
only), the separate *Hilega Milega* open-source project, and paid
infrastructure (explicitly excluded — zero-cost constraint).

### 1.1 Design goals (from the BRD)

| # | Goal | Architectural consequence |
|---|---|---|
| G1 | Usable from a phone, 3 office days/week (laptop unavailable) | Web app on free cloud; mobile-responsive Streamlit UI |
| G2 | Multi-category screening (Intraday / Breakout / Long-term / Global Macro / Event-Driven) | Pluggable **strategy registry** + category map separating *scan* vs *panel* kinds |
| G3 | Automated Telegram alerts incl. **one-day-prior expiry reminder** | Background scheduler thread with IST job table + dedupe state |
| G4 | ₹0 hosting | Streamlit Community Cloud free tier + free external keep-alive |
| G5 | Secrets never in git and never in chat | Loader pattern + encrypted Secrets store + in-app Fyers login (server-side exchange) |
| G6 | Fast iteration (laptop → phone) | Git-push auto-redeploy pipeline; no manual server ops |

---

## 2. System overview

The system is a single Streamlit application that combines:

1. A **password-gated dashboard** (categories, scans, results, history, panels).
2. A **scan engine** with a pluggable strategy registry over Fyers market data.
3. **Standalone panels** (Global Macro, Event-Driven) that do not touch the NSE scan engine.
4. A **scheduler thread** that sends Telegram messages at fixed IST times.
5. **Fyers authentication** performed entirely server-side via a paste-box (auth_code never leaves the owner's devices except as an HTTP POST to Fyers).

### 2.1 High-level architecture (block diagram)

```
                        ┌──────────────────────────────────────────────────┐
                        │            OWNER DEVICES (clients)              │
                        │  Phone (office, 3 days/wk) · Laptop (home)      │
                        │  Any modern browser · HTTPS only                │
                        └───────────────┬──────────────────────────────────┘
                                        │ 1. HTTPS (dashboard password)
                                        ▼
   ┌───────────────────────────────────────────────────────────────────────────┐
   │                    STREAMLIT COMMUNITY CLOUD (free tier)                  │
   │   App: https://stockscreener2026.streamlit.app      Python 3.12           │
   │                                                                           │
   │  ┌──────────────┐   ┌───────────────────┐   ┌──────────────────────────┐   │
   │  │ Password gate │──▶│ UI layer (app.py) │──▶│ Panels                  │   │
   │  │ (session)     │   │ sidebar · tabs    │   │  · Global Macro         │   │
   │  └──────────────┘   │ history & stats   │   │  · Event-Driven (IPO)   │   │
   │                     └────────┬──────────┘   └──────────────────────────┘   │
   │                              │ Run Scan                                    │
   │                              ▼                                             │
   │  ┌───────────────────────────────────────────────┐                         │
   │  │ Engine (engine.run_screener)                  │                         │
   │  │  registry → fetch-once → compute → signals    │                         │
   │  └───────┬───────────────────────────┬───────────┘                         │
   │          │                           │                                     │
   │  ┌───────▼────────┐          ┌───────▼──────────────┐                      │
   │  │ Persistence    │          │ Scheduler thread     │                      │
   │  │ DuckDB (data/) │          │ jobs 09:00 / 15:45   │                      │
   │  │ token · state  │          │ IST · 60-min grace   │                      │
   │  └────────────────┘          └───────┬──────────────┘                      │
   │                                      │                                     │
   │  ┌───────────────────────────────────▼──────────────────────────────────┐  │
   │  │ config/keys.py — secret loader (env → file → st.secrets → default)  │  │
   │  │ values only in .streamlit/secrets.toml (gitignored) / Cloud Secrets │  │
   │  └──────────────────────────────────────────────────────────────────────┘  │
   └──────┬───────────────────┬───────────────────┬──────────────────┬──────────┘
          │                   │                   │                  │
          ▼                   ▼                   ▼                  ▼
   ┌─────────────┐   ┌──────────────┐   ┌────────────────┐   ┌───────────────┐
   │ Fyers API v3│   │ Market meta  │   │ Telegram Bot   │   │ Apify (IPO)   │
   │ NSE/BSE OHLC│   │ Finnhub →    │   │ API (alerts)   │   │ + NSE cross-  │
   │ index quotes│   │ TwelveData → │   │                │   │ check         │
   │ (primary)   │   │ yfinance     │   │                │   │               │
   └─────────────┘   └──────────────┘   └───────▲────────┘   └───────────────┘
                                                │
   ┌──────────────────────┐                     │ scheduled + scan messages
   │ UptimeRobot (free)   │──── HTTP GET ───────┘ (wakes/keeps container alive)
   │ every 10 min         │──── HTTPS ──────────▶ app URL
   └──────────────────────┘

   ┌──────────────────────┐   git push (main)   ┌────────────────────────────┐
   │ Laptop (dev)         │────────────────────▶│ GitHub private repo        │
   │ editor · tests · CLI │                     │ ssahu08/stock_screener     │
   └──────────────────────┘                     │ → auto-redeploy 2–5 min    │
                                                └────────────────────────────┘
```

### 2.2 Design patterns used

| Pattern | Where | Why |
|---|---|---|
| **Strategy + Registry** | `strategies/base.py`, `strategies/registry.py` | New setups drop in via `@register_strategy` — engine/UI never change |
| **Template method** | `Strategy` ABC (`compute` → `buy_signal`/`sell_signal` → `format_metrics`/`sort_value`) | Uniform contract for CLI, engine, UI, stats |
| **Chain of responsibility (fallback)** | Global-macro data sources: Finnhub → TwelveData → yfinance | Survives any single provider outage/throttle |
| **Idempotent singleton thread** | `core/scheduler.start_scheduler()` | Streamlit reruns the script often; the job thread must start exactly once per process |
| **Loader (indirection) for secrets** | `config/keys.py` | Committed code contains no secrets; location of secrets differs per environment |
| **Gate-before-render** | Top of `app.py` | Nothing (no data, no widgets) renders before authentication |

---

## 3. Tech stack

| Layer | Technology | Version / notes | Choice rationale |
|---|---|---|---|
| Language | Python | 3.12 (Cloud build) | Ecosystem for quant/data; matches Streamlit support matrix |
| Web framework | Streamlit | 1.63 (`st.link_button`, `width="stretch"`) | Zero-ops Python dashboards; per-session script model; free hosting |
| UI testing | `streamlit.testing.v1.AppTest` | bundled | In-process smoke tests without a browser |
| Market data (NSE) | Fyers API v3 | `fyers-apiv3` | Owner's broker; OHLC for ~2,300 NSE symbols + index quotes |
| Market data (global) | Finnhub (primary) → TwelveData (no key) → yfinance | `requests`, `yfinance` | Free-tier resilience; yfinance as universal fallback |
| IPO data | Apify actors | `APIFY_TOKEN` + `requests` | Scraped IPO subscription/listing data |
| Alternative data | yfinance, BeautifulSoup | `beautifulsoup4` | NSE cross-checks, index summaries |
| Persistence | DuckDB | single-file `data/screener.duckdb` | Zero-server analytical store; schema migration helper in `storage/db.py` |
| Cache/aux files | JSON / TXT in `data/` | `scheduler_state.json`, `fyers_token.txt`, IPO cache | Simple, inspectable, gitignored |
| HTTP | `requests` | — | All integrations |
| Numerics | pandas, numpy | — | Indicator math |
| Notifications | Telegram Bot API | `sendMessage` (plain text, no parse_mode) | Free, instant to phone; plain text avoids Markdown-escaping failures |
| Hosting | Streamlit Community Cloud | free tier | G4 (₹0), git-push deploys, encrypted Secrets store |
| Source control / CI | GitHub (private repo) | push = deploy | G6 |
| Keep-alive | UptimeRobot (free) | HTTP GET / 10 min | Counters the ~12 h idle sleep |
| Secret config | TOML (`.streamlit/secrets.toml`) | — | Native Streamlit secrets integration |

**Requirements file:** `requirements.txt` (8 packages: streamlit, pandas,
numpy, requests, duckdb, fyers-apiv3, yfinance, beautifulsoup4).

---

## 4. Application architecture

### 4.1 Code map

```
stock_screener/
├── app.py                     # Streamlit entry: gate → login box → categories → tabs → history
├── engine.py                  # run_screener(): scan orchestration (CLI + library)
├── config/
│   ├── keys.py                # SECRET LOADER (committed, contains no values)
│   └── settings.py            # UNIVERSE_SETTINGS, STRATEGY_SETTINGS (thresholds)
├── core/
│   ├── auth.py                # Fyers auth: build_auth_url / exchange_auth_code / token IO
│   ├── data.py                # NSE universe (EQUITY_L, SERIES=EQ) + OHLC fetching
│   ├── alerts.py              # send_telegram_alert() for scan results
│   └── scheduler.py           # IST job thread: SOD/EOD/expiry-reminder (see §4.7)
├── strategies/
│   ├── base.py                # Strategy ABC (contract, §4.4)
│   ├── registry.py            # STRATEGY_REGISTRY + @register_strategy
│   ├── hilega_milega.py       # weekly RSI-9/WMA/EMA momentum
│   ├── volcano.py             # squeeze family: Watch/Blast/Snap/Coil/Darvas/Blast60
│   ├── expiry_credit_spread.py# regime-aware expiry logic + BULL_PUT/BEAR_CALL engine
│   ├── ipo_listing.py         # IPO listing-gains scan strategy
│   ├── ipo_listing_verifier.py# Apify + NSE cross-check verifier
│   ├── utils/indicators.py    # shared indicators
│   └── global_macro/
│       ├── index_follower.py  # US ±1% → ETF signals, SOD plan, EOD summary
│       ├── data_sources.py    # Finnhub / TwelveData / yfinance chain
│       ├── config.py          # ETF universe (MON100, MONQ50, MASPTOP50, MAFANG), ₹10k
│       └── alerts.py          # format_sod_telegram / format_eod_summary / send_telegram
├── storage/db.py              # DuckDB persistence + stats + history queries
├── data/                      # RUNTIME (gitignored, ephemeral on cloud)
│   ├── screener.duckdb        # scan results (keeps 2 scans/strategy)
│   ├── fyers_token.txt        # access token (daily, never displayed)
│   └── scheduler_state.json   # last-sent date per job
├── .streamlit/secrets.toml    # ALL SECRETS (gitignored; mirrored in Cloud Secrets)
├── CLOUD_SETUP.md             # deployment runbook
└── docs/                      # this document set
```

### 4.2 UI layer (`app.py`)

Render order is security-first:

```
1. UTF-8 stdout reconfigure (Windows cp1252 emoji crash guard)
2. PASSWORD GATE  ── if locked: render box, st.stop()   ← before ALL imports
3. imports (config, engine, scheduler start)
4. token status + in-app Fyers login box (rendered & st.stop() if no valid token)
5. SIDEBAR: category checkboxes → universe radio → price band → alert toggle →
   🚀 Run Scan (disabled without token) → 🔒 Log out
6. MAIN: one tab per selected category
        ├─ kind="scan"        → render_scan_category()   (engine-backed)
        ├─ global_macro panel → render_global_macro()    (ETF only, no NSE engine)
        └─ event_driven panel → render_event_driven()    (IPO verifier)
7. HISTORY & STATS: per-strategy metrics + last 200 rows
```

**Category map** (single source of truth, `CATEGORIES` in `app.py`):

| Key | Label | Kind | Strategies |
|---|---|---|---|
| `long_term` | 📈 Long Term | scan | `hilega_milega` |
| `breakout` | 🚀 Breakout / BTST | scan | `volcano_watch`, `volcano_blast`, `volcano_snap`, `coil_break`, `darvas_box` |
| `intraday` | ⚡ Intraday | scan | `volcano_blast_60`, `expiry_credit_spread_nifty`, `expiry_credit_spread_banknifty` |
| `global_macro` | 🌍 Global Macro | **panel** | `index_follower` (4 ETFs — never enters NSE engine) |
| `event_driven` | 📋 Event-Driven | **panel** | `ipo_listing_gains` |

`kind` drives routing: only `scan` strategies are passed to `run_screener`;
panels render standalone and may run without Fyers NSE data constraints.

### 4.3 Scan data flow

```
sidebar 🚀 Run Scan
   │  strategy_key="volcano_watch,volcano_blast,…" · universe=nifty50|full
   │  min_price · max_price · send_alert
   ▼
engine.run_screener(...)
   │ 1. resolve keys → STRATEGY_REGISTRY (invalid keys rejected)
   │ 2. core.data: build universe (NSE EQUITY_L, SERIES=EQ, ₹min–₹max)
   │ 3. FETCH ONCE per ticker (ThreadPool, max_workers=4) and SHARE across
   │    all selected strategies — a bench run costs one download per stock
   │ 4. per strategy: compute(df) → indicators
   │                   buy_signal(df) / sell_signal(df) → boolean rows
   │                   format_metrics() → readable column
   │                   sort_value() → rank BUYs
   │ 5. persist results → DuckDB (prune: keep 2 most recent scans/strategy)
   │ 6. if send_alert: Telegram summary of top 15 BUYs (top_n_display)
   ▼
DataFrame (columns incl. strategy_key, scan_time, ticker, signal,
           current_price, key_metrics, weekly_change_pct, hist_avg_return)
   ▼
UI: per-category tabs · History & Stats section
```

**Fyers data constraints** (drive configuration):

| Constraint | Value | Design response |
|---|---|---|
| 1-day bars range | max **366 days** | `min_history_days=365`; weekly strategies need ≥22 bars (`min_weekly_bars`) |
| Intraday bars history | max **100 days** | `volcano_blast_60` limited to ~100d; documented in README |
| Timestamps | UTC epoch | converted on read; backtests compare in IST/UTC consistently |
| Access token | expires daily **~06:30 IST** | token-valid-for-today check; daily login flow; UTC server-date caveat (safe after ~06:00 IST) |
| Index symbols | `NSE:NIFTY50-INDEX`, `NSE:NIFTYBANK-INDEX` | used by expiry strategy |

### 4.4 Strategy contract (`strategies/base.py`)

Every strategy implements:

| Member | Purpose |
|---|---|
| `name` / `key` | display name / stable id (used in DB, UI, CLI) |
| `timeframe` | `weekly` \| `daily` \| `60min` |
| `category` | strategy's own classification tag |
| `compute(df)` | add indicator columns (pure, returns df) |
| `buy_signal(df)` / `sell_signal(df)` | boolean Series aligned to df.index |
| `format_metrics(df)` | human-readable metrics cell |
| `sort_value(df)` | ranking key for BUY rows |
| `min_bars` | minimum history guard |

Registration: `@register_strategy` instantiates the class and stores
`{name, compute_func, buy_condition, sell_condition, metrics_formatter,
sort_metric, strategy_obj, timeframe}` keyed by `key`.

**Strategy catalog (11 registered):**

| Key | Name | TF | Category | Core rule (summary) |
|---|---|---|---|---|
| `hilega_milega` | Hilega Milega | weekly | Long Term | RSI-9 > 50 AND WMA-21(RSI) > 50 AND close > prior week high (SELL mirrors) — *in 2-week live trial* |
| `volcano_watch` | Volcano Watch | daily | Breakout | BB(10) squeeze **forming** — advance watchlist |
| `volcano_blast` | Volcano Blast | daily | Breakout | squeeze → close > upper band + 1.5× volume |
| `volcano_snap` | Volcano Snap | daily | Breakout | BB(10) squeeze + 1.2× volume for 4–6-session coils |
| `coil_break` | Coil Break | daily | Breakout | Bollinger-inside-Keltner squeeze → expansion trigger |
| `darvas_box` | Darvas Box | daily | Breakout | flat 20-bar box → close above box high |
| `volcano_blast_60` | Volcano Blast 60 | 60 min | Intraday | Blast rules on hourly bars |
| `expiry_credit_spread_nifty` | Expiry Credit Spread (NIFTY) | intraday | Intraday | expiry-day credit spread (§4.6) |
| `expiry_credit_spread_banknifty` | Expiry Credit Spread (BANKNIFTY) | intraday | Intraday | monthly-only variant |
| `index_follower` | Index Follower | daily | Global Macro | US close ±1% → Indian ETF signal (§4.8) |
| `ipo_listing_gains` | IPO Listing Gains | event | Event-Driven | IPO listing-gain scan + verifier (§4.9) |

*Squeeze family shares ONE exit rule (close below band mid after a recent
squeeze) so ENTRY variants stay directly comparable — a deliberate test-bench
design reverse-engineered from the owner's chart screenshots.*

### 4.5 Engine & persistence

- **Engine** (`engine.py`): `run_screener(...)` is the single entry point for
  CLI (`python engine.py …`) and UI; UTF-8 logging; results tagged with
  `strategy_key`.
- **Persistence** (`storage/db.py`): DuckDB file with schema migration; keeps
  the **2 most recent scans per strategy** (older pruned) to bound size on an
  ephemeral disk; exposes `get_strategy_stats()` (scans, BUY rows, evaluated
  BUYs, avg 4-week forward return, win rate) and `get_scan_history()`.
- **Forward-return stats** use `forward_weeks=4`; they are *historical
  statistics of past signals*, not predictions.

### 4.6 Expiry Credit Spread (design-critical: NSE expiry regimes)

The strategy models **Bull Put** and **Bear Call** credit spreads on index
options with hard intraday risk discipline.

**Time rules (stored as UTC; IST = UTC+5:30):**

| Parameter | IST | UTC |
|---|---|---|
| Entry window | **09:30 – 11:30** | 04:00 – 06:00 |
| Forced exit (no overnight) | **14:30** | 09:00 |
| Base chart interval | 15 min | — |

**Entry filters:** expiry day ∧ in window ∧ trend strength (ADX ≥ 20) ∧
VWAP reclaim (2 consecutive candles) ∧ volume > 1.0× 20-period average.

**Risk:** fixed intraday stop = 1.5 × ATR(14); target credit = 35% of spread
width; max loss 2% of capital per spread; max 2 concurrent spreads.

**Configuration (`SpreadConfig`):**

| Field | NIFTY | BANKNIFTY |
|---|---|---|
| `lot_size` | **65** | **30** (SEBI notional band revision, eff. Jan-2026) |
| `strike_step` | 50 | 100 |
| `spread_width_pct` / `short_strike_pct` | 1.0% / 1.0% OTM of spot | same |
| strikes | ATM = round(spot/step)·step; short OTM; long = short − width | same |

**Expiry-day regime logic** (must match NSE circulars — this is where an
earlier Thursday-based design was corrected):

| Constant | Value | Meaning |
|---|---|---|
| `TUESDAY_RULE_FROM` | **2025-09-01** | NSE derivatives expire **Tuesday** from Sep-2025 (circular NSE/FAOP/68685); before that, Thursday |
| `_NIFTY_TRANSITION_GAP` | 2025-09-01 → 2025-09-09 | 04-Sep-2025 expiry moved to 09-Sep-2025 — **no expiry existed 01–08 Sep 2025** |
| `BANKNIFTY_MONTHLY_ONLY_FROM` | **2024-11-20** | BANKNIFTY **weekly options discontinued** → monthly only (last trading day of month; last Thu pre-Sep-2025, last Tue after) |

- `compute_expiry_dates` / `compute_expiry_mask` are regime-aware and apply
  **holiday adjustment** (expiry on an exchange holiday moves to the previous
  trading day) via the trading calendar.
- `is_expiry_day(dt, index_name)` powers the scheduler's D-1 reminder.
- NIFTY: every Tuesday post-gap. BANKNIFTY: exactly one expiry per month.
- *Consequence:* all pre-Sep-2025 Thursday backtests and pre-existing
  BANKNIFTY weekly backtests are invalid and must be re-run (roadmap §11).

### 4.7 Scheduler (`core/scheduler.py`)

| Property | Design |
|---|---|
| Timezone | **IST = fixed UTC+5:30** (`timezone(timedelta(hours=5, minutes=30))`) — India has no DST, so no `tzdata` dependency and no server-local-tz bugs (Cloud runs UTC) |
| Jobs | `sod_summary` **09:00**, `eod_summary` **15:45** (every day, per owner's "everyday" requirement) |
| Execution | daemon thread, one per process, `start_scheduler()` idempotent (module-level thread + lock) |
| Tick | every 30 s: `run_due_jobs(datetime.now(IST))` |
| Due rule | fire if `now ∈ [job_time, job_time + 60 min]` **and** not already sent today → **catch-up window** absorbs restarts; no evening spam |
| Dedupe state | `data/scheduler_state.json` → `{"sod_summary": "YYYY-MM-DD", …}`; state written **only on successful send** (failed sends retry each tick) |
| Session dependency | Streamlit executes app code **per browser session**, and the scheduler starts **after the gate** → one *visit + unlock* per process lifetime starts it (the daily Fyers login does this naturally); keep-alive keeps the container awake but does not execute the script |
| One-instance rule | Two instances (local + cloud) have separate state files → duplicate messages. Cloud is the production sender; local runs with `SCREENER_SCHEDULER_DISABLE=1` or is stopped |
| Standalone mode | `python core/scheduler.py` runs the same loop outside Streamlit |
| Test hook | env `SCREENER_SCHEDULER_DISABLE=1` suppresses all sends |

**Message composition:**

- **09:00 SOD** = Index Follower plan (US close % + signals + major moves)
  + NIFTY previous completed session (O/H/L/C + %, via yfinance `^NSEI`)
  + **D-1 expiry reminder** when `is_expiry_day(tomorrow)` → lists which
  indexes expire (NIFTY weekly/monthly, BANKNIFTY monthly), the trade plan
  (entry 09:30–11:30, forced exit 14:30) and current lots (65/30 — read from
  the registry so they can never drift), plus a holiday-shift caveat.
- **15:45 EOD** = NIFTY today O/H/L/C from hourly bars (falls back to
  "no session today" on holidays/weekends).

### 4.8 Global Macro — Index Follower

```
US close (4 indices)              Indian ETFs (4)
S&P 500 · Nasdaq 100      →       MON100  ← S&P 500
Dow Jones · Russell 2000  →       MONQ50  ← Nasdaq 100
                                  MASPTOP50 ← Dow Jones
                                  MAFANG   ← Nasdaq/PM theme
   ┌─ source chain: Finnhub → TwelveData (unavailable, no key) → yfinance ─┐
   └─ rule: |close change| ≥ 1% → LONG (≥+1%) / SHORT (≤−1%) signal        │
       capital ₹10,000/trade · entry next session 09:15 · exit discretionary│
```

- `get_sod_plan()` fetches indices **once** and builds the plan (signals +
  major-move alerts >2%); `format_sod_telegram` / `format_eod_summary`
  render it.
- Manual trade log lives in session state (`gm_trades`) — **not persisted**,
  hence EOD messages never claim "no trades".
- Panel shows live ETF quotes for the 4 symbols only; it never calls the NSE
  scan engine (fast, works without a full universe fetch).

### 4.9 Event-Driven — IPO Listing Gains

`ipo_listing.py` (scan strategy) + `ipo_listing_verifier.py` (verification
engine): Apify-sourced IPO data (subscription, issue details, listing
performance) cross-checked against NSE sources; views for upcoming/open
IPOs and listing outcomes. Uses `APIFY_TOKEN`; results cached in `data/`.

### 4.10 Notification layer

| Channel | Trigger | Module |
|---|---|---|
| Scan summary (top 15 BUYs) | manual Run Scan with `send_alert` ✓ | `core/alerts.send_telegram_alert` |
| SOD / EOD / expiry reminder | scheduler | `strategies.global_macro.alerts.send_telegram` (plain text, **no parse_mode** — immune to Markdown-escape 400s) |

All sends are no-ops when `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are absent.

---

## 5. Security design

### 5.1 Secrets inventory

| Secret | Local location | Cloud location | In git? |
|---|---|---|---|
| Fyers `CLIENT_ID`, `SECRET_KEY`, `REDIRECT_URI` | `.streamlit/secrets.toml` | Cloud **Settings → Secrets** (encrypted) | ❌ |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | same | same | ❌ |
| `APIFY_TOKEN`, `FINNHUB_TOKEN` | same | same | ❌ |
| `DASHBOARD_PASSWORD` | same | same | ❌ |
| Fyers access token | `data/fyers_token.txt` (server disk) | ephemeral `data/` | ❌ never displayed |
| Daily `auth_code` | transient (browser → paste → POST to Fyers) | same | ❌ never logged |
| Loader `config/keys.py` | committed | committed | ✅ contains **no values** |

### 5.2 Controls

1. **Gate-first rendering.** The password check runs before any other
   rendering/import; locked ⇒ box only + `st.stop()`. Comparison is
   constant-time (`secrets.compare_digest`). Sidebar provides **Log out**.
2. **In-app Fyers login (Option A hardened for mobile).** Flow:
   `build_auth_url()` → owner opens Fyers login on phone → Fyers redirects to
   `redirect_uri?auth_code=…` → owner **copies that URL** and pastes it into
   the dashboard → `extract_auth_code()` → `exchange_auth_code()` posts
   code+client_id+secret **server-side** to Fyers → token written to
   `data/fyers_token.txt`. The auth_code is valid ~2 min and never appears in
   chat, email, git, or client-side JS.
3. **Git hygiene.** `.gitignore`: `.streamlit/secrets.toml`, `data/`, `logs/`,
   `.env`, `*.csv`. Pre-commit ritual: grep the staged diff for known secret
   substrings. **History purge:** `git filter-repo --invert-paths --path
   config/keys.py --force` rewrote every commit (old SHAs are dead; any old
   clone must be re-cloned).
4. **Rotation procedure** (also `CLOUD_SETUP.md` §6): if a repo is ever made
   public — rotate Fyers SECRET_KEY (api-dashboard.fyers.in), Telegram bot
   token (@BotFather /revoke) + chat id, Apify token, Finnhub key — then
   update both the local file and the Cloud Secrets box.
5. **Standing rule:** tokens/auth_codes/passwords are never pasted into chat
   transcripts; docs point at file locations instead of quoting values.

### 5.3 Residual risks (accepted / monitored)

| Risk | Notes | Mitigation |
|---|---|---|
| Streamlit GitHub OAuth `repo` scope | needed to deploy private repos; gives code read/write | Acceptable: repo contains **no secrets** (purged); Streamlit never receives secret values via git |
| App URL is public; security = password | Community Cloud has no built-in auth on free tier | Strong `DASHBOARD_PASSWORD`; gate before render; optional email allow-list if tier allows |
| Cloud Secrets UI screen-sharing | secrets visible if screen shared during edit | don't share screen while editing secrets |
| Keep-alive monitor sees app URL | URL not secret (password protects content) | — |

---

## 6. Infrastructure & environments

### 6.1 Topology

```
 DEV (laptop, home)                 VCS                        PROD (cloud)
┌────────────────────┐   push   ┌───────────────────┐   build  ┌────────────────────────────┐
│ Editor / CLI       │────────▶│ GitHub            │────────▶│ Streamlit Community Cloud  │
│ python engine.py   │  main   │ ssahu08/          │ 2–5 min │ stockscreener2026.         │
│ AppTest suites     │         │ stock_screener    │         │ streamlit.app (Python 3.12)│
│ streamlit run (dev) │         │ (PRIVATE)         │         │ encrypted Secrets store    │
└────────────────────┘         └───────────────────┘         │ ephemeral data/ disk       │
                                                             └──────┬──────────────┬───────┘
                                                        visits       │              │ HTTPS APIs
                                                             ▲       ▼              ▼
                                              ┌──────────────┴──┐  ┌─────────────────────────┐
                                              │ Phone (office)  │  │ Fyers · Finnhub ·       │
                                              │ UptimeRobot ping│  │ yfinance · Apify ·      │
                                              │ every 10 min     │  │ Telegram                 │
                                              └─────────────────┘  └─────────────────────────┘
```

### 6.2 Environments

| | DEV | PROD |
|---|---|---|
| Where | laptop `D:\stock_screener` | Streamlit Community Cloud |
| Run | `streamlit run app.py` / `python engine.py` | auto on push |
| Secrets | local `.streamlit/secrets.toml` | Cloud Secrets store (paste-synced) |
| Data | persistent local `data/` | **ephemeral** — resets every redeploy/restart |
| Scheduler | disabled for alerting while cloud is live (`SCREENER_SCHEDULER_DISABLE=1`) | **the** alert sender |
| Purpose | coding, tests, backtests | phone access, scheduled alerts |

### 6.3 Cloud configuration (as deployed)

| Setting | Value |
|---|---|
| Repository / branch / entrypoint | `ssahu08/stock_screener` · `main` · `app.py` |
| Python | 3.12 |
| Deploy trigger | every `git push` to main (auto, 2–5 min) |
| Secrets | Cloud **Settings → Secrets** (mirror of local file, 8 keys incl. `DASHBOARD_PASSWORD`) |
| Keep-alive | UptimeRobot HTTP GET, 10-min interval |
| Free-tier limits | ~1 GB RAM · **sleep after ~12 h idle** · ephemeral disk · **UTC clock** · **1 private-app slot + unlimited public** (Hilega Milega's repo is public, so the private slot belongs to this app) |
| Clock caveat | "token valid for today" compares the **UTC date** → logins after ~06:00 IST are always safe; late-evening logins can invalidate ~30 min early |

### 6.4 Operational rules

1. **One alert-sending instance** (cloud while laptop could also run).
2. **Visit + unlock once per process lifetime** to start the scheduler
   (daily Fyers login does this).
3. **After every push:** `data/` resets → Fyers re-login required; at most one
   duplicate scheduled message per job (state reset).
4. **Rollback** = `git revert` + push (same pipeline).
5. **Sleep recovery:** UptimeRobot wakes the container; a human visit
   restarts app code/scheduler.

---

## 7. External integrations

| Service | Purpose | Auth | Limits / failure handling |
|---|---|---|---|
| Fyers API v3 | NSE/BSE OHLC, index quotes, symbol master | OAuth-style: `auth_code` → `access_token` (daily) | 1D ≤366d, intraday ≤100d; token errors → login box UX |
| Finnhub | US index closes (primary) | `FINNHUB_TOKEN` | rate limits → chain falls through |
| TwelveData | (secondary) | none configured | skipped ("unavailable") — placeholder in chain |
| yfinance | US fallback + NIFTY session summaries | none | occasional cloud-IP throttle → next tick/retry |
| Apify | IPO data | `APIFY_TOKEN` | per-actor pricing on free credits; cache in `data/` |
| Telegram Bot API | all messages | bot token + chat id | plain text; sends are best-effort with logging |
| UptimeRobot | keep-alive | monitor URL | — |

**Data-source policy:** anything user-visible degrades gracefully — partial
messages (e.g., SOD without US section) rather than silence or crashes; all
builder functions catch and embed the failure reason.

---

## 8. Testing & quality

| Suite | Scope | Status |
|---|---|---|
| Cloud checks (20) | secret loader paths, auth helpers, gate lock/wrong/unlock, login box, Run Scan disabled | ✅ passing |
| Scheduler checks (27) | due/grace/dedupe logic, reminder on weekly-only vs monthly eves, message composition, state round-trip, live NIFTY fetch | ✅ passing |
| Expiry checks (33) | regime boundaries, transition gap, holiday shift, real 2026 NSE dates (15/29 Sep), monthly count | ✅ passing |
| AppTest smoke | dashboard renders with scheduler wired (env-disabled) | ✅ passing |
| Live E2E | real Telegram delivery (test + first real SOD on 26-Sep) | ✅ verified |
| Secret scan | staged diff grep for known secret strings before each push | ✅ ritual |

**Known test gap:** no automated browser (UI interaction beyond AppTest);
Fyers refresh-token flow untested live (needs one real interaction).

---

## 9. Limitations & risk register

| # | Limitation / risk | Impact | Mitigation / plan |
|---|---|---|---|
| L1 | Free tier sleeps after ~12 h idle | alerts stop | UptimeRobot keep-alive; daily visit |
| L2 | Scheduler needs a visit+unlock per process | alerts dormant after redeploy until visited | daily login habit documented; future: external cron (§11) |
| L3 | Daily Fyers token (~06:30 IST) | morning login required | in-app box (~30 s); refresh-token flow on roadmap |
| L4 | `data/` ephemeral | token/history/cache lost on redeploy | re-login after pushes; export history if needed |
| L5 | yfinance/Finnhub throttling from cloud IPs | missing US section | fallback chain + retry-on-next-tick (state written only on success) |
| L6 | Expiry logic is weekday+calendar based | exchange-holiday expiry shifts | regime functions adjust via trading calendar; D-1 message carries holiday caveat |
| L7 | Old backtests (Thursday / BN-weekly) invalid | misleading stats if reused | re-run on Tuesday regime before any live use (roadmap) |
| L8 | Two instances ⇒ duplicate alerts | noise | one-instance rule; disable flag |
| L9 | 1-GPU/1-GB free container | heavy full-NSE scan risk | `Nifty 50 (fast)` for mobile; workers=4; ~2–4 min full scans measured OK |
| L10 | Strategy stats ≠ future performance | misinterpretation | disclaimers in README/UI/BRD |

---

## 10. Delivery plan

| Phase | Scope | Status |
|---|---|---|
| **P0 — Foundation** | registry engine, 7 core strategies, DuckDB, CLI, local dashboard, Option-A auth | ✅ done |
| **P1 — Cloud & mobile** | password gate, in-app Fyers login, secrets loader + Cloud Secrets, git-history purge, README/CLOUD_SETUP | ✅ done |
| **P2 — Automation** | scheduler (SOD/EOD/D-1 expiry), NSE Tuesday-regime expiry fix, lot/strike corrections | ✅ done (26-Sep) |
| **P3 — Trial & hardening** | production soak (owner testing), bug backlog, first Tuesday expiry session (paper), Hilega Milega 2-week review | 🔄 current |
| **P4 — Backlog** | see §11 | planned |

## 11. Roadmap (prioritized backlog)

1. **Fyers refresh-token flow** — test live; reduce daily logins toward the
   15-day validity (unblocks true zero-touch mornings).
2. **Re-run Expiry Credit Spread backtests on the Tuesday regime** — all
   historical Thursday/BANKNIFTY-weekly results are void.
3. **Paper-trade ledger for the credit spread** — track the 09:30–11:30 /
   14:30 discipline before any capital (owner is new to F&O).
4. **Open=Low strategy + folder reorg + `StrategyCategory` + sidebar radio**
   (previously approved, not started).
5. **Hardened alert sender:** external cron (e.g., GitHub Actions schedule)
   posting to Telegram — removes L2 dependency on visits.
6. **Hilega Milega trial review** at the end of the 2-week window.
7. Optional: app-level viewer allow-list, CSV export buttons, dark theme
   polish.

---

## Appendix A — Configuration reference

**`UNIVERSE_SETTINGS` (`config/settings.py`)**

| Key | Value | Meaning |
|---|---|---|
| `min_price` / `max_price` | 50 / 2000 | default sidebar price band (₹) |
| `min_history_days` | 365 | ≤ Fyers 366-day cap |
| `min_weekly_bars` | 22 | WMA(21) needs 22 weekly bars |
| `max_workers` | 4 | parallel fetch threads |
| `top_n_display` | 15 | top BUYs in console/Telegram |
| `forward_weeks` | 4 | forward-return horizon for stats |

**Runtime flags:** `SCREENER_SCHEDULER_DISABLE=1` (no scheduled sends).

## Appendix B — Glossary

| Term | Definition |
|---|---|
| IST | Indian Standard Time, UTC+5:30 (fixed, no DST) |
| Expiry day | Last trading day of an index-options contract (NSE: Tuesday since Sep-2025) |
| Lot | Minimum options contract size (NIFTY 65, BANKNIFTY 30 as of Jan-2026) |
| Credit spread | Selling one option and buying a farther strike — collects premium with defined risk |
| Squeeze | Bollinger Bands compressing inside Keltner/narrow range before expansion |
| auth_code | One-time, ~2-minute Fyers authorization code from the redirect URL |
| SOD / EOD | Start / End of Day (09:00 / 15:45 IST messages) |
| Keep-alive | External pinger that prevents idle sleep of the free hosting container |
| Regime | A historical rule period for expiry calculation (Thursday-era / transition / Tuesday-era) |
