# Business Requirements Document (BRD) — NSE Stock Screener

| | |
|---|---|
| **Document** | Business Requirements Document |
| **Version** | 1.0 |
| **Date** | 26-Sep-2026 |
| **Status** | Draft for review (requirements confirmed with the business owner) |
| **Product owner** | Sahu (retail trader; sole end user in Phase 1–3) |
| **Companion docs** | [Technical Design Document](technical-design-document.md) · [User Guide](user-guide.md) |

### Revision history

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 26-Sep-2026 | Sahu / AI assistant | Initial BRD capturing stated inputs and derived requirements |

---

## 1. Executive summary

The owner is a retail trader in Indian markets who currently runs a
Python-based NSE stock screener **on a laptop at home only**. On the three
days each week spent in the office, the laptop is unavailable — making the
existing tool unusable exactly when market decisions are being made.

This project delivers the screener as a **password-protected web app on free
cloud hosting**, usable from the owner's phone anywhere, expanded from a
handful of technical scans into a **five-category trading research system**
(Intraday, Breakout, Long-term, Global Macro, Event-Driven), and augmented
with **automated Telegram notifications** — most critically a
**one-day-prior expiry reminder** so no derivatives expiry is ever missed.

Constraints that shape every requirement: **zero hosting cost**, and
**broker credentials/auth codes must never appear in chat transcripts or
version control**.

## 2. Business background

| Fact (stated by the owner) | Implication |
|---|---|
| Works in office 3 days/week; laptop stays at home 2 days | Mobile-first access is mandatory, not nice-to-have |
| Existing screener runs locally (`streamlit run app.py`) | Migration to cloud, not a rewrite |
| New to F&O (futures & options) | Guardrails: backtest before trading, paper-trade first, forced intraday exits, reminders ahead of expiry |
| Running a separate *Hilega Milega* strategy in a 2-week live trial | That codebase/project must remain untouched; the trial continues as-is |
| Had an incorrect expiry understanding (Thursday) | Requirements include **verified** NSE expiry rules (Tuesday since 01-Sep-2025) |
| Explicit standing rule: tokens/auth codes never pass through chat | Security requirement with process (not just technical) enforcement |

## 3. Business objectives & success measures

| # | Objective | Success measure |
|---|---|---|
| BO-1 | Trade research available on the phone during office days | Dashboard usable on a mobile browser over cellular; Nifty-50 scan ≤ ~60 s |
| BO-2 | Never miss an expiry | D-1 Telegram reminder received on the day before every NIFTY/BANKNIFTY expiry |
| BO-3 | Stay informed daily without opening charts | 09:00 IST message with US close + signals + NIFTY prior session; 15:45 IST message with NIFTY session summary |
| BO-4 | Operate at ₹0 recurring cost | Hosting, alerts, and monitoring all on free tiers |
| BO-5 | Protect broker/API credentials | Zero secrets in git history (audited), zero secrets in chat |
| BO-6 | Shorten the fix loop | Owner reports bug → fix on laptop → `git push` → live in ≤ ~5 min without touching the server |

## 4. Scope

### 4.1 In scope
- Web dashboard (password-gated) with five categories and 11 strategies.
- Cloud deployment (Streamlit Community Cloud), auto-redeploy on git push.
- Daily Fyers login **from inside the dashboard** (no terminal on the phone).
- Telegram notifications: scan summaries + scheduled SOD/EOD/D-1-expiry.
- Index Follower (US → Indian ETF) and IPO Event-Driven panels.
- Keep-alive monitoring; documentation set (TDD, BRD, User Guide, setup runbook).

### 4.2 Out of scope (explicitly)
- **Order placement / trade execution** — research and alerts only (Fyers login is for market data).
- Hosting the separate *Hilega Milega* repository (owner keeps it as-is for its live trial).
- Paid infrastructure of any kind.
- Native mobile app / APK (rejected in favour of the web option).
- Multi-user tenancy, public sharing of the dashboard.
- Guaranteed profits / advisory services (the tool surfaces candidates; decisions remain the owner's).

## 5. Stakeholders

| Stakeholder | Role | Interest |
|---|---|---|
| Owner (Sahu) | Business owner + sole daily user | Mobile access, alerts, correctness of expiry logic, security |
| Future maintainers | Dev/ops | Documentation, testability, clean architecture |
| Fyers / Telegram / data providers | External dependencies | API terms, rate limits (constraints on the solution) |

## 6. Functional requirements

Priority: **M** = Must (Phase 1–2 delivered) · **S** = Should · **C** = Could
*Source column traces each requirement to the owner's stated inputs.*

| ID | Requirement | Prio | Acceptance criteria | Source |
|---|---|---|---|---|
| **FR-01** | **Password-gated dashboard.** Nothing renders before authentication; wrong password rejected; logout available. | M | Locked screen shows only a password box; correct password reveals the app; sidebar Log out re-locks. | "password-gated dashboard" |
| **FR-02** | **Mobile-first cloud access.** Full app usable from a phone browser (office days, no laptop). | M | URL opens on mobile; categories, scan, results, history all operable; Nifty-50 scan completes on cellular. | "3 office days/2 home days… laptop can't run in office"; Option B chosen |
| **FR-03** | **In-app Fyers login.** Owner obtains `auth_code` via the broker's login page and pastes the redirect URL into the dashboard; the exchange runs server-side. | M | Paste of a valid redirect URL signs the session in (~30 s); token stored server-side, never displayed; expired/invalid code gives a clear retry. | "in-app Fyers login (paste auth_code URL in dashboard, exchange server-side)"; never-in-chat rule |
| **FR-04** | **Five scan/panel categories** in one dashboard: Intraday, Breakout/BTST, Long Term, Global Macro, Event-Driven. | M | Categories selectable in sidebar; each renders its strategies in a tab; panels never run the NSE engine. | "multi-category trading system (Intraday/Breakout/Long-term/Global Macro/Event-Driven)" |
| **FR-05** | **Strategy set (11)** across categories, including the squeeze test-bench family and Hilega Milega. | M | All 11 registered strategies appear in CLI + UI; a bench run shares one data fetch per stock. | "expand… multi-category"; existing strategies retained |
| **FR-06** | **Run Scan** over a selectable universe (Nifty 50 fast / Full NSE ~2,300) with price band and optional Telegram summary. | M | Nifty-50 ≈ 30 s, Full ≈ 2–4 min; results table + history persisted; top-15 BUY alert sent when enabled. | existing screener behaviour carried to cloud |
| **FR-07** | **Scheduled Telegram: D-1 expiry reminder.** On the day before any NIFTY/BANKNIFTY expiry, a message names the date, expiring indexes, entry/exit discipline, and lot sizes. | M | Message arrives on the evening/morning before every expiry day (weekday-based + holiday caveat); no reminder on non-expiry eves. | *"auto scheduling notifications on telegram 1 days prior"* |
| **FR-08** | **Scheduled Telegram: daily US market close** in the 09:00 IST message (S&P 500, Nasdaq 100, Dow, Russell % + ±1% ETF signals). | M | Message contains the four US closes + signals/alerts; degrades gracefully (partial message) if a provider fails. | *"Everyday US market close price"* |
| **FR-09** | **Scheduled Telegram: NIFTY open/close summary** — previous session at 09:00; today's session at 15:45. | M | 09:00 shows prior session O/H/L/C + %; 15:45 shows today's O/H/L/C + % (holiday line on non-trading days). | *"Nifty open and close summary"*; timing per earlier 9:00/15:45 plan |
| **FR-10** | **Correct NSE expiry rules** — Tuesday expiries since 01-Sep-2025 (incl. Sep-2025 transition gap), BANKNIFTY monthly-only since 20-Nov-2024, holiday shifts; lots NIFTY 65 / BANKNIFTY 30. | M | 33 automated checks pass against real 2026 NSE dates; Thursday-era logic removed. | *"verify the expiry days for NSE… should not be Thursdays anymore"* |
| **FR-11** | **Expiry Credit Spread strategy** with entry window 09:30–11:30 IST, forced exit 14:30, no overnight holding, defined-risk parameters. | M | Signals/backtests respect windows & regime; message texts carry the same discipline. | new-to-F&O guardrails; NK-Sir strategy replaced |
| **FR-12** | **Scan history & statistics** — per-strategy scans, BUY counts, avg 4-week forward return, win rate; last 200 rows. | S | History section shows the five metric cards + filterable table after scans. | "screens… history, stats" (existing dashboard) |
| **FR-13** | **Zero-touch redeploy** — code changes reach the phone via git push only. | M | Push to `main` → app rebuilt in 2–5 min → phone refresh shows the change. | "laptop dev → git push → cloud auto-redeploys → phone just refreshes" |
| **FR-14** | **Keep-alive** so scheduled alerts are not lost to idle sleep. | M | External monitor pings every ≤10 min; app does not sleep; documented in runbook. | zero-cost + reliable alerts |
| **FR-15** | **IPO Event-Driven panel** — upcoming/listed IPO data cross-checked (Apify + NSE). | S | Panel renders IPO views without touching the scan engine. | Event-Driven category |
| **FR-16** | **Global Macro panel** — 4-ETF quotes, manual trade log (₹10k/trade). | S | Panel shows live ETF quotes + trade log for MON100/MONQ50/MASPTOP50/MAFANG. | Index Follower decision (±1% rule) |
| **FR-17** | **Single-instance alerting rule** (cloud is the sole sender) documented and enforced by process/config. | S | Duplicate-message scenario documented; local sender disable flag exists. | derived from reliability goal |
| **FR-18** | **Documentation set**: TDD, BRD, User Guide, cloud runbook. | S | Three docs + `CLOUD_SETUP.md` in repo; any future user can onboard from the User Guide alone. | *"prepare these documents… anyone who uses this app in future"* |

## 7. Business rules

| ID | Rule |
|---|---|
| BR-01 | NSE index derivatives expire **Tuesday** (01-Sep-2025 onward); on an exchange holiday, expiry moves to the **previous trading day**. |
| BR-02 | BANKNIFTY has **monthly expiry only** since 20-Nov-2024 (last trading day of the month). |
| BR-03 | Lot sizes: **NIFTY 65**, **BANKNIFTY 30** (SEBI notional band, eff. Jan-2026); strikes step 50 / 100. |
| BR-04 | Credit-spread trades: entry only **09:30–11:30 IST on expiry day**; **forced exit by 14:30**; **never hold overnight**. |
| BR-05 | Index Follower: signal only when a US index closes **≥ +1% / ≤ −1%**; capital **₹10,000 per ETF trade**; exit by owner discretion. |
| BR-06 | Default scan price band **₹50–₹2,000**; universe = NSE `EQUITY_L`, `SERIES=EQ`. |
| BR-07 | **No secrets in git, no secrets in chat.** Tokens/auth codes exist only in the encrypted secrets store / server disk / broker session. |
| BR-08 | Scheduled messages are sent **at most once per day per job**; retries occur only within a 60-minute catch-up window. |
| BR-09 | While a strategy is in **live trial** (Hilega Milega), its logic is frozen; trial results are reviewed before any changes. |
| BR-10 | No capital is deployed on the Expiry Credit Spread until backtests are **re-run on the Tuesday regime** and a paper-trial period completes (owner is new to F&O). |

## 8. Non-functional requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Cost | ₹0 recurring spend (hosting, monitoring, alerts on free tiers). |
| NFR-02 | Availability | App awake during market days; scheduled alerts delivered (keep-alive + daily visit). Residual risk documented (12-h sleep, redeploy reset). |
| NFR-03 | Performance | Nifty-50 scan ≈ 30 s (mobile-usable); full NSE ≤ 4 min; UI interactive on 4G. |
| NFR-04 | Security | Gate-before-render; constant-time password compare; secrets encrypted at rest in cloud; git history audited clean; rotation procedure documented. |
| NFR-05 | Reliability of alerts | Failed sends retry within the window; state written only on success; partial data still yields a partial message, never a crash. |
| NFR-06 | Portability | Windows laptop (dev) + any modern mobile/desktop browser (use); no OS-specific installs on the phone. |
| NFR-07 | Maintainability | Strategy = one file + one registration; docs current with code; automated test suites gate changes (20/27/33 checks + smoke). |
| NFR-08 | Data accuracy | Expiry calendar validated against real NSE dates; source fallbacks for all market data; index data via broker (Fyers) preferred for NSE. |
| NFR-09 | Compliance tone | Tool presents research candidates only; disclaimers present (no advice, no guarantees, historical stats ≠ future performance). |

## 9. Assumptions & dependencies

| # | Assumption / dependency |
|---|---|
| A-1 | Owner holds an active Fyers account **with API access** and a market-data subscription. |
| A-2 | Owner's phone has a modern browser; Telegram app installed for alerts. |
| A-3 | GitHub account (`ssahu08`) hosts the private repo; Streamlit Cloud is authorized for it (private-app slot available — the other app uses a public repo). |
| A-4 | Free-tier limits are acceptable: ~1 GB RAM, 12-h idle sleep, ephemeral disk, UTC clock, one private app. |
| A-5 | External free keep-alive monitor (UptimeRobot/cron-job.org) remains configured. |
| A-6 | Data providers (Fyers, Finnhub, yfinance, Apify) honour their free/quota terms; outages degrade gracefully rather than blocking. |
| A-7 | Owner performs the ~30-second daily Fyers login until the refresh-token flow is validated. |
| A-8 | Existing Thursday-based backtest results are considered void pending re-run (BR-10). |

## 10. Risks & mitigation

| # | Risk | Likelihood/Impact | Mitigation |
|---|---|---|---|
| R-1 | Cloud app asleep → missed D-1/expiry alert | M / H | Keep-alive ping; daily visit; roadmap item: external cron sender |
| R-2 | Daily Fyers login fatigue → skipped mornings | M / M | In-app 30-s flow; refresh-token work on roadmap |
| R-3 | Secrets leak (repo made public / screen share) | L / H | Purged history, encrypted store, rotation checklist in runbook |
| R-4 | Duplicate alerts (two instances) | M / L | One-instance rule + disable flag, documented |
| R-5 | Wrong expiry date acted upon (holiday) | L / H | Regime+calendar logic; holiday caveat inside the reminder itself |
| R-6 | New-to-F&O owner over-trades signals | M / H | BR-10 paper-first rule; forced-exit design; disclaimers |
| R-7 | yfinance/Finnhub throttling on cloud IPs | M / L | Fallback chain; retry-on-success state |
| R-8 | Free tier changes/deprecations | L / M | All infra replaceable (docs describe each piece); code portable |

## 11. Acceptance criteria (product-level DoD)

The product is accepted for the trial phase when:

1. ✅ Owner signs in from the phone with the dashboard password and completes a full Nifty-50 scan on mobile.
2. ✅ In-app Fyers login works from the phone without a terminal.
3. ✅ The 09:00 and 15:45 Telegram messages arrive on a normal trading day (first real SOD delivered 26-Sep-2026).
4. ✅ A **D-1 expiry reminder** arrives the day before an actual expiry (next test: Mon 28-Sep → Tue 29-Sep, NIFTY weekly+monthly & BANKNIFTY monthly).
5. ✅ Zero secrets in `git log` (verified by history purge audit).
6. ✅ `git push` produces a live update without server access.
7. ✅ Documentation set (TDD, BRD, User Guide) exists in-repo and matches behaviour.

## 12. Delivery phases

| Phase | Content | Status |
|---|---|---|
| P0 | Local engine, strategies, dashboard, Option-A auth | ✅ |
| P1 | Cloud migration: gate, in-app login, secrets model, history purge, runbook | ✅ |
| P2 | Automation: scheduler (SOD/EOD/D-1), NSE expiry verification & fix | ✅ |
| P3 | Owner trial: bug backlog, first real expiry session (paper), Hilega Milega review | 🔄 |
| P4 | Backlog: refresh-token flow, Tuesday-regime backtests, paper ledger, Open=Low + reorg, external cron sender | planned |

## 13. Glossary

See the [TDD glossary](technical-design-document.md#appendix-b--glossary) and
[User Guide glossary](user-guide.md#15-glossary). Key terms: **SOD/EOD**
(start/end-of-day messages), **D-1** (one day prior), **expiry day**, **lot**,
**credit spread**, **auth_code**, **keep-alive**.
