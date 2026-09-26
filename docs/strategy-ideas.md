# Strategy Ideas & Backlog (Durable Memory)

> Purpose: strategy ideas are recorded here **the moment they are proposed**, so they
> survive session summarization. If you hear a new idea, append it before replying.

---

## 1. Open=Low (a.k.a. "Strategy 1") — BUILT · CALIBRATED 26-Sep-2026

**Status:** implemented on `feature/bb-pivot-test` as three registry entries —
`open_low_1d` / `open_low_1w` / `open_low_1m` — under their own dashboard category
**🕯️ Open=Low** (own category until the folder reorg lands). 55/55 unit checks,
AppTest smoke pass, Nifty-50 live scan pass (2 BUYs on 26-Sep close), replay-validated
on Fyers.

### Calibration (owner decision, 26-Sep-2026 — measured on SBIN / Fyers, 7 years)

Two rules of the original brief proved unworkable on real data and were put to the
owner with measured numbers:

| Question | Measurement | **Owner decision** |
|---|---|---|
| Supertrend gate | Same-bar flip + exact O&L = **0 signals in 7 years** (SBIN 1D); owner's brief said *"best would be* during the change" | **Trend must be BULLISH** when the candle forms; flip **not** required same-bar. Flip recency shown in metrics as entry-timing context (`flip now` / `N bars ago`) |
| 1M wick | Exact O==L on monthly candles = **0 of 83 months** (SBIN); owner's own Feb-2021 example has a **0.83% wick** | **Wick ≤ 1%** on 1M only. 1D/1W stay **exact** (measured fire rates 2.9% of days / 2.0% of weeks — usable) |

Post-calibration replay results (SBIN, 7y, Fyers): **1D = 24 signals** (avg +5d
+1.05%, 67% > 0) · **1W = 2 signals** · **1M = 3 signals — first one is the owner's
own example, 2021-02-26 `O==L ✓ | ST bull ✓ | 1.3×avg ✓`**.

### Owner's original brief (verbatim)

> Strategy 1 : [NAME- SOMETHING LIKE OPEN=LOW]
>
> Idea- ( Would not say it is for intraday) ( Can be also use for investment puposes/very long term)
> Entry point- (Example- SBIN chart on (1M), 2021 February). Best would be during change of
> Supertrend from bearish to bullish( Espically for highger timeframe 1W, 1M ).
> Exit- Based on users discreation.
> Logic : Open Price=Low Price (1D, 1W, 1M)
> Alert : Telegram(Should have good details for this)
>
> Basic UI-
> 1. Should have top 5 from each (1D, 1W, 1M).
> 2. And since 1st daily candle of the month if it is Open=Low on (1D).
>    We should save it for a review table since that stock would potentially be a candidate
>    for bull trend in that month.
> 3. Placement within Dashboard/code can be anywhere based on idea and our plan to
>    reorganization of entire project structure.

### Agreed refinements (as built, incl. 26-Sep calibration)

| Parameter | Decision |
|---|---|
| Pattern test | 1D / 1W: **exact `Open == Low`** · 1M: **wick ≤ 1%** (monthly candles always wick — calibration) |
| Confirmation | **Supertrend (10,3) trend must be bullish** (same-bar flip not required — calibration); flip recency printed in metrics |
| Volume gate | > 50,000 **and** > rolling average (20 bars on 1D/1W, **6 months on 1M** — matches the ~12-candle production fetch) |
| Timeframes | 1D / 1W / 1M as three registered strategies → three tabs = top picks per TF (engine top-15 slice) |
| Exit | User discretion (informational strategy, no auto-exit — `sell_signal` never fires) |
| Monthly review | 1st **trading** day of month: if that 1D candle is O==L → `monthly_open_low` DuckDB row (ACTIVE/CHECKED/CLOSED), auto-captured once per month after a Nifty-50 scan + manual **🔄 Refresh** button; month must END before the 1M signal confirms (fires first scan after month-end) |
| Alerts | Via the existing engine Telegram digest when a scan runs with alerts on (detailed per-signal metrics strings) |
| UI | **🕯️ Open=Low** category: 3 TF tabs + persistent "📋 Monthly Open=Low Review" table (renders even without a scan) |
| Monthly 1M ST note | Engine history cap (366 days ≈ 12 monthly candles) also motivated trend-check over flip-check; `min_bars=190` daily |

### Reference material (on disk)

- `dataset/*.JPG` — 13 owner chart screenshots: `OL Weekly 1-3.JPG`, `OL Monthly 1-3.JPG`,
  `best example - …JPG` (Feb / Apr / Jun / Jul / May / Jan), `Till 9 Sep.JPG`, `Jan 1.JPG` etc.

### Building blocks already in the codebase

- `strategies/utils/indicators.py` → `supertrend(df, 10, 3.0)`,
  `open_equals_low(df, tolerance=0.0)`, `volume_filter(df, min_avg_volume=50000, period=20)`
- Scheduler plumbing exists (`core/scheduler.py`, 09:00 / 15:45 IST) — reuse vs the
  original 9:30/17:00 times is an open integration decision

### Build checklist

- [x] `strategies/open_low.py` — three TFs, calibrated gates, metrics with flip timing
- [x] `strategies/open_low_tracker.py` + `monthly_open_low` DuckDB table (1st-trading-day capture, idempotent, once-per-month auto + refresh button)
- [x] Registry: `open_low_1d` / `open_low_1w` / `open_low_1m`
- [x] Dashboard: 🕯️ Open=Low category, 3 tabs, persistent review table
- [x] `tests/test_open_low.py` — 55/55 checks
- [x] Replay validation on Fyers (SBIN: 1D 24 / 1W 2 / 1M 3 incl. Feb-2021 example)
- [x] Engine Telegram digest carries detailed signal metrics (strings per row)
- [ ] Dedicated SOD/EOD scan jobs at 9:30 / 17:00 (original plan) — today alerts ride the manual scan; see open question 2

### Open questions

1. ~~Exact `==` vs tolerance~~ — **answered 26-Sep-2026**: exact 1D/1W, ≤1% 1M
2. Fold alerts into the existing 09:00 / 15:45 windows or add dedicated 9:30 / 17:00 jobs
3. ~~Final category~~ — own 🕯️ category until the folder reorg, then re-place

---

## 2. Other standing backlog (BRD P4 / TDD roadmap)

| Item | Status |
|---|---|
| BB-Pivot Washout (Idea 1 shelf + Idea 2 washout close) | built on `feature/bb-pivot-test`, awaiting owner chart validation |
| Open=Low 1D/1W/1M + monthly review table | built on `feature/bb-pivot-test`, calibrated 26-Sep, replay-validated — awaiting owner review |
| Folder reorg + `StrategyCategory` + sidebar radio | approved, not started |
| Expiry Credit Spread backtests re-run on Tuesday regime | **required before Tue 29 Sep paper session** |
| Fyers refresh-token flow test | needs one live owner interaction |
| External cron sender (single-instance hardening) | optional |
| Hilega Milega trial review (~10/7) | due ~early Oct |
