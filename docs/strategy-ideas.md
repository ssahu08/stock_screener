# Strategy Ideas & Backlog (Durable Memory)

> Purpose: strategy ideas are recorded here **the moment they are proposed**, so they
> survive session summarization. If you hear a new idea, append it before replying.

---

## 1. Open=Low (a.k.a. "Strategy 1") — SPEC LOCKED · NOT YET BUILT

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

### Agreed refinements (from discussion)

| Parameter | Decision |
|---|---|
| Pattern test | **Exact `Open == Low`** (tolerance param exists in code, default 0.0) |
| Confirmation | **Supertrend (10, 3)** flip bearish → bullish (esp. 1W / 1M) |
| Volume gate | > 50,000 **and** > 20-day average volume |
| Timeframes | 1D, 1W, 1M (multi-TF: report top-5 per timeframe) |
| Exit | User discretion (informational strategy, no auto-exit) |
| Monthly review | 1st **trading** day of month: if that 1D candle is Open=Low → row in `monthly_open_low` DuckDB table (candidate for bull trend that month) |
| Alerts | Telegram, detailed (SOD 9:30 AM + EOD 5:00 PM per original plan) |
| UI | Top-5 per TF + monthly review table; category placement tied to folder reorg |

### Reference material (on disk)

- `dataset/*.JPG` — 13 owner chart screenshots: `OL Weekly 1-3.JPG`, `OL Monthly 1-3.JPG`,
  `best example - …JPG` (Feb / Apr / Jun / Jul / May / Jan), `Till 9 Sep.JPG`, `Jan 1.JPG` etc.

### Building blocks already in the codebase

- `strategies/utils/indicators.py` → `supertrend(df, 10, 3.0)`,
  `open_equals_low(df, tolerance=0.0)`, `volume_filter(df, min_avg_volume=50000, period=20)`
- Scheduler plumbing exists (`core/scheduler.py`, 09:00 / 15:45 IST) — reuse vs the
  original 9:30/17:00 times is an open integration decision

### Not yet built

- [ ] `open_low` strategy module (scanner across 1D/1W/1M with Supertrend flip + volume gate)
- [ ] `monthly_open_low` DuckDB table + monthly tracker (1st trading day capture)
- [ ] Registry entry (category = pending reorg; currently would live in 🧪 Test Bench)
- [ ] Dashboard views: Top-5 per TF + monthly review table
- [ ] Telegram detail format (SOD/EOD)

### Open questions

1. Exact `==` vs tiny tick tolerance (spec says exact — keep unless charts prove otherwise)
2. Fold alerts into the existing 09:00 / 15:45 windows or add dedicated 9:30 / 17:00 jobs
3. Final category after the folder reorg (Long-Term vs its own)

---

## 2. Other standing backlog (BRD P4 / TDD roadmap)

| Item | Status |
|---|---|
| BB-Pivot Washout (Idea 1 shelf + Idea 2 washout close) | built on `feature/bb-pivot-test`, awaiting owner chart validation |
| Folder reorg + `StrategyCategory` + sidebar radio | approved, not started |
| Expiry Credit Spread backtests re-run on Tuesday regime | **required before Tue 29 Sep paper session** |
| Fyers refresh-token flow test | needs one live owner interaction |
| External cron sender (single-instance hardening) | optional |
| Hilega Milega trial review (~10/7) | due ~early Oct |
