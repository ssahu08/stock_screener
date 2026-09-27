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

## 2. DOJI Candle Watch (test strategy) — BUILT · CALIBRATED 28-Sep-2026

**Status:** spec clarified, rule calibrated against the owner's chart marks, tool built —
`strategies/doji.py` + `tools/doji_scanner.py` + `tests/test_doji.py` (74/74 checks).
**Test-only — must NOT reach the prod app** (standalone tool, no registry/app/scheduler
wiring; cloud deploys `main`, this lives on the feature branch).
Pending: owner's threshold/volume decision (see “Open decision” below) + first live day.

### Owner's original brief (verbatim, 27-Sep-2026)

> I want a build a test strategy to find out DOJI candles for timeframes
> (2 times a day notification) 10min, (EOD) 1 hour, 1 D, 1 W, 1 M.
> Scope- Nifty 50 only
> No need to add to prod application.

### Owner's clarifications (verbatim, 28-Sep-2026)

> Live scan throughout the day at regular intervals (should capture all dojis formed before this time).
> (Additonal) - Good to have Doji at lower BB and above a pivot/support
> It will be used as informational watchlist
> 1W / 1M - Completed candle
> Telegram during testing to get notified
> I would read these signals and learn to create a strategy out of it.
> Objective behind this one- To catch these doji formations and get alerts ( as per my
> observations generally these gets create at 10:25 AM, 11:35 AM, 12:15PM, 13:35PM )
> We can extend scope for Nifty 100 as well.

### Spec (as understood)

| Item | Decision |
|---|---|
| Pattern | DOJI on **10min** (intraday) + **60min / 1D / 1W / 1M** (EOD) |
| Doji rule (owner) | **body ≤ 10% of (high−low)**, inclusive; no colour condition (“black doji” = the yellow-marked pattern) |
| Intraday behaviour | **Live scan at regular intervals all day** (default every 15 min); each scan catches **all dojis formed up to that time** (catch-up on completed bars, deduped — same-day window) |
| Observed formation times (owner) | 10:25, 11:35, 12:15, 13:35 IST — all on the 9:15+10n candle grid → scan cadence must cover them |
| Notification (owner) | **Send on every scan that has new finds** — one message per scan, each doji reported **once ever** (state `data/doji_state.json`); 1H/1D/1W/1M = **EOD pass** (15:45) |
| Higher TFs | 1W / 1M = **last completed candle only** (no in-progress); 1D = today after 15:30, else last completed |
| Purpose | **Informational watchlist** — no buy/sell semantics; owner reads signals to learn toward a future strategy |
| Context (owner) | **Tag, not filter**: 📉 lower-BB(20,2) touch + 🛟 within ±0.5% of P/S1/S2/S3 (prev session's daily pivots intraday; prev candle on 1D/1W/1M) |
| Universe | **Nifty 50** default (48 unique symbols today), **extendable to Nifty 100** (`--universe` stub errors until wired) |
| Production | **Never** — standalone test tool on feature branch, not registered in the app |

### Chart examples — calibration ground truth (owner's yellow marks, 28-Sep)

All **10-minute** Zerodha charts; marked candles = the definition of "doji" to match:

| Symbol | Visible dates | Notes |
|---|---|---|
| COALINDIA | 21/09–24/09 (2026) | several marks, incl. gap-up area 22/09; RSI pane, no BB on chart |
| TATAPPOWER | 12/08–17/08 (2026) | marks near **lower BB**; BB study visible |
| FEDERALBNK | (vertical line, dates not shown) | 3 marks — one at mid dip, two near top-right |
| CASTROLIND | (squeeze visible) | 1 mark inside BB squeeze |
| BANKBARODA | (lower-BB dip) | 1 mark at decline into **lower BB** |

Note: FEDERALBNK / CASTROLIND are outside Nifty 50 → supports the Nifty-100 extension.
“Black doji” clarified by owner: **just the yellow-marked pattern** — no colour rule.

### Calibration decisions (owner, via questionnaire, 28-Sep-2026)

1. Doji rule: **body ≤10% of range** (classic, inclusive) — calibrate on the marks, decide with numbers
2. Context: **tag, not filter** — every doji listed, context as badges
3. “Black doji”: **just the yellow-marked pattern** — no colour condition
4. 10-min cadence: **send on every scan with new finds** (each doji reported once)

### Calibration results (measured 28-Sep-2026, Fyers 10-min, vs the owner's marks)

Match vs the yellow marks (rule = body ≤10% of range):

| Mark | Result |
|---|---|
| COALINDIA 24/09 cluster (~419–421) | ✅ 4 zero-body dojis at 420.05–421.25 + one at 7.7% — matches the cluster |
| COALINDIA 21/09 (~410.5), 22/09 (~424.5) | ✅ 0–9.1% candidates within ±1% of the marked prices (screenshot price-reading ±) |
| TATAPOWER 12/08 (~376), 13/08 (~379), 14/08 (~384) | ✅ exact-date candidates at **0.0–8.3%** body/range |
| TATAPOWER 17/08 (~380) | ⚠️ best that day = **12.5%** — rule misses it. Notably that day's candidates at the owner's observed times **10:25 / 12:15 / 13:35 IST** are 12.5–22.2% (quiet small candles) → threshold edge case, owner to verify against screenshot |
| FEDERALBNK / CASTROLIND / BANKBARODA (no dates on screenshots) | ✅ ≤0% candidates at every marked price level (dates listed in scan output) |

Noise rates (Fyers, 5 example symbols, per stock per day):

| Threshold | ≤5% | **≤10% (current)** | ≤15% | ≤25% |
|---|---|---|---|---|
| dojis/stock/day | 2.3 | **4.3** | 6.8 | 11.2 |
| ≈ Nifty-50 total/day | 110 | **208** | 325 | 535 |

- **Min-range filter NOT needed** at ≤10%: “dead” candles (range < 0.25×ATR14) = **7 of 633**
  candidates (~1%); median candidate range = 0.15–0.19% of price (0.68–0.84× ATR) — marked
  candles all carry real wicks. `min_range_atr` stays 0 in `DojiConfig`.
- **Bug found & fixed during calibration:** Fyers **intraday stamps are UTC** (session arrives
  as 03:45–10:00) — `fetch_frame` now converts 10/60-min frames to naive IST, otherwise the
  completion gate would evaluate the live forming candle and messages would print UTC times.
  Regression-tested (74th check group in `tests/test_doji.py`).

### Build checklist — DONE (28-Sep-2026)

- [x] `strategies/doji.py` — pure detection: `DojiConfig`, `is_doji`, `detect_dojis`
      (context computed over the whole frame, then filtered), BB + support tags (reuses
      `bb_pivot_reversion._pivot_lines`), completed-candle helpers, weekly/monthly resamples,
      Telegram formatting/chunking
- [x] `tools/doji_scanner.py` — standalone runner: catch-up intraday scan + EOD pass,
      dedup state (`data/doji_state.json`, per-TF age pruning), injectable fetch/send,
      `--once | --eod | --loop [--interval N] [--eod-at HH:MM] [--no-alert] [--dry-run]
      [--body-max N]`; dedups the duplicated ONGC; known-bad TATAMOTORS counted as fetch
      failure, no crash; **does not touch** registry/app/scheduler
- [x] `tests/test_doji.py` — **74/74 checks** (`python tests/test_doji.py`)
- [x] Live smoke scan on real data: 48 syms, graceful failures, 0 state writes (`--dry-run`)
- [ ] First live day + owner review of real alerts

Run (laptop, feature branch, after `python core/auth.py`):

```
python tools/doji_scanner.py --loop            # full live day: 10m every 15 min + EOD 15:45
python tools/doji_scanner.py --once --no-alert # one console-only scan
python tools/doji_scanner.py --eod             # EOD pass alone (after 15:30)
```

### Open decision (28-Sep-2026, numbers presented to owner)

Message volume vs the mark-match gap — owner to pick after seeing the table:

1. **Stay ≤10%, 15-min scans** (as decided): ~208 dojis/day → likely **~20–25 messages/day**
2. **30-min scans**: ~10–15 messages/day (bigger batches, same coverage)
3. **Back to the original 2 batched digests/day** (11:45 + 15:45): 2 messages, same catch-up
4. **Widen to ≤15%** (captures the 17/08 TATAPOWER candles): +56% volume (~325/day)

### Building blocks available

- `strategies/utils/indicators.py` (no doji helper yet — needs `body/range` ratio); BB + pivot
  logic exists in `strategies/bb_pivot_reversion.py`
- `core/alerts.py → send_telegram_alert()`; **do NOT touch `core/scheduler.py`** (prod — cloud
  runs it from `main`); standalone scanner loop instead
- Engine frames: daily (+weekly resample), 60min fetch; **10min frame does not exist yet**
  (needs `resolution=10` fetch — Fyers caps intraday at 100 days; today-only fetch is trivial)
- `tools/replay_bb_pivot.py` for replaying any frame; monthly resample pattern in
  `strategies/open_low.py`; universe via `core/data.get_nifty50_symbols()` (+Nifty-100 later)

---

## 3. Other standing backlog (BRD P4 / TDD roadmap)

| Item | Status |
|---|---|
| BB-Pivot Washout (Idea 1 shelf + Idea 2 washout close) | built on `feature/bb-pivot-test`, awaiting owner chart validation |
| Open=Low 1D/1W/1M + monthly review table | built on `feature/bb-pivot-test`, calibrated 26-Sep, replay-validated — awaiting owner review |
| Folder reorg + `StrategyCategory` + sidebar radio | approved, not started |
| Expiry Credit Spread backtests re-run on Tuesday regime | **required before Tue 29 Sep paper session** |
| Fyers refresh-token flow test | needs one live owner interaction |
| External cron sender (single-instance hardening) | optional |
| Hilega Milega trial review (~10/7) | due ~early Oct |
