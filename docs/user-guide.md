# User Guide — NSE Stock Screener

*Everything an end user (today or in the future) needs to run this app.*

| | |
|---|---|
| **Document** | User Guide |
| **Version** | 1.0 · 26-Sep-2026 |
| **App URL** | `https://stockscreener2026.streamlit.app` |
| **Companion docs** | [BRD](brd.md) · [Technical Design Document](technical-design-document.md) · `README.md` (developer quickstart) · `CLOUD_SETUP.md` (deployment runbook) |

---

## 1. About this guide

**Who it's for:** anyone who operates this dashboard — the owner today, any
future user or maintainer tomorrow. It assumes **no programming knowledge**;
every technical term is explained in the [Glossary](#16-glossary).

**How it's organised:**

| § | Section | Use it when… |
|---|---|---|
| 2–4 | What the app is · requirements · access | first time |
| 5–6 | Daily routine · login steps | every trading morning |
| 7 | Dashboard tour | learning the UI |
| 8 | Category & strategy reference | deciding *what a signal means* |
| 9 | Expiry Credit Spread playbook | expiry-day trading |
| 10 | Telegram alerts reference | interpreting messages |
| 11–12 | Mobile tips · troubleshooting | something looks wrong |
| 13 | Operations & maintenance | upkeep, updates, secrets |
| 14–16 | FAQ · safety · glossary | curiosity |

### 1.1 Quick reference card

```
EVERY TRADING MORNING (≈1 minute)
  1. Open https://stockscreener2026.streamlit.app on your phone
  2. Enter dashboard password
  3. Daily Fyers login:  🔓 Open Fyers Login → log in → copy the URL
                        that contains auth_code= → paste → ✅ Sign in
  4. Sidebar: pick categories → Universe = Nifty 50 (fast) → 🚀 Run Scan
  5. Read §10 Telegram: 09:00 US-close message should already be waiting

KEY NUMBERS
  NSE expiry day ................ Tuesday (since 01-Sep-2025)
  BANKNIFTY expiry .............. last Tuesday of the month (monthly only)
  Lots ......................... NIFTY 65 · BANKNIFTY 30
  Credit-spread entry ........... 09:30–11:30 IST on expiry day
  Forced exit ................... 14:30 IST — never hold overnight
  Alerts ........................ 09:00 IST (SOD) · 15:45 IST (EOD) · D-1 expiry
  Fyers token ................... expires ≈06:30 IST daily
  Scan speed .................... Nifty 50 ≈30 s · Full NSE ≈2–4 min
```

---

## 2. What the app is (and isn't)

A **research dashboard** that scans Indian stocks and index derivatives for
technical setups, tracks global-market influence on Indian ETFs, verifies
IPO listing data — and pushes the important findings to your Telegram.

**It does:** screen NSE stocks · show historical statistics of each setup ·
send scheduled market messages · remind you **one day before expiry**.

**It does NOT:** place orders (Fyers login is for market data only) ·
guarantee profits · give SEBI-registered investment advice. Every signal is
a *candidate for your review*.

### 2.1 The five categories

| Tab | Kind | What it covers |
|---|---|---|
| 📈 **Long Term** | NSE scan | Weekly momentum/trend (Hilega Milega — in 2-week live trial) |
| 🚀 **Breakout / BTST** | NSE scan | Daily "squeeze → expansion" breakout family + Darvas boxes |
| ⚡ **Intraday** | NSE scan | Hourly volatility breakout + **Expiry Credit Spreads** |
| 🌍 **Global Macro** | panel (no NSE scan) | US close ±1% → 4 Indian ETF signals, quotes, trade log |
| 📋 **Event-Driven** | panel (no NSE scan) | IPO listing-gains tracker (Apify + NSE cross-check) |

---

## 3. Requirements

- Any modern browser (Chrome/Edge/Safari, phone or laptop) + internet.
- An **active Fyers account with API access** (market-data subscription).
- *(For alerts)* a Telegram account — the bot already chats with the owner.
- Nothing to install — no APK, no app store.

---

## 4. Access & first-time setup

1. **Open the app URL** (above). You'll see the **password screen** —
   nothing else renders until you authenticate.
2. **Where's the password?** The `DASHBOARD_PASSWORD` value in
   `.streamlit/secrets.toml` (owner's laptop) or Streamlit Cloud
   *Settings → Secrets*. This guide never prints it.
3. Enter it → the dashboard loads. Bookmark the URL on your phone.

> **Security model:** the app is on a public URL but gated by this password
> (checked before anything renders). Broker credentials are *never* in the
> page, the URL, or git — see §13.

---

## 5. The daily morning routine

Fyers **access tokens expire every day around 06:30 IST**, so each trading
morning starts with a ~30-second login (§6.2), then:

1. **Sidebar → 📂 Categories**: tick what you want today
   (scan categories feed the engine; panels always render).
2. **Universe**: `Nifty 50 (fast)` for mobile/quick looks;
   `Full NSE (~2,300)` when you want the whole market (~2–4 min).
3. Price band defaults **₹50–₹2,000** — adjust if you screen outside it.
4. **🚀 Run Scan** (enabled only after login). Watch the spinner.
5. Read results per category tab (§7.3), download CSV if you want.
6. Check **📜 Scan History & Stats** below the tabs.
7. Skim the **09:00 Telegram message** (§10) for US close + expiry reminders.

*Evenings:* the **15:45 message** gives NIFTY's session summary; on
non-trading days it says "no session today".

---

## 6. Logging in

### 6.1 Dashboard password

- Typed at the gate; a wrong password keeps you on the lock screen.
- Sidebar **🔒 Log out** re-locks (useful on shared devices).
- Forgot it? It's the `DASHBOARD_PASSWORD` line in the secrets file
  (owner only).

### 6.2 Daily Fyers login (the 🔓 box)

When the sidebar shows *"🔐 Not logged in — use the Fyers login box below"*,
the main area shows the login box:

1. Tap **🔓 Open Fyers Login** → the Fyers login page opens (same device).
2. Log in with your Fyers credentials/2FA.
3. You land on a **redirect page** — its address bar contains `auth_code=`.
   **Copy the full URL immediately** (valid ≈ **2 minutes**):
   - **Android Chrome:** tap the address bar → copy. If the URL is hidden:
     **⋮ → Share → Copy link**.
   - **iPhone Safari:** tap the URL → *Copy*.
   - **Desktop:** click the address bar → Ctrl+C.
4. Back in the dashboard tab → paste into
   *"Paste the redirect URL (or auth_code):"* → **✅ Sign in**.
5. *"Logged in! Loading dashboard…"* → you're in. The token lives on the
   **server** (`data/fyers_token.txt`) and is never displayed.

| Error you see | Cause | Fix |
|---|---|---|
| "auth_code expires ~2 minutes after login…" | too slow copying | redo step 1–4 faster |
| "Invalid auth_code" / exchange failed | partial URL or reused code | copy the *whole* URL; each code works once |
| Box reappears next morning | normal — daily expiry | repeat the flow |
| Box reappears after a redeploy | `data/` resets on deploy | repeat the flow (§13.3) |
| Page won't load (cloud waking up) | app was sleeping | wait ~10 s, refresh |

**Security note:** the code travels browser → *your* dashboard → Fyers
(exchanged server-side). Never paste it into chat, email, or screenshots.

---

## 7. Dashboard tour

### 7.1 Sidebar — "📈 Screener Controls"

| Control | What it does |
|---|---|
| ✅/🔐 token line | "✅ Fyers token valid for today" or "🔐 Not logged in…" |
| **📂 Categories** checkboxes | Choose which categories are active (all on by default); hover ℹ️ for its description |
| **Universe** radio | `Nifty 50 (fast)` ≈30 s · `Full NSE (~2,300)` ≈2–4 min (shown in the caption) |
| **Min ₹ / Max ₹** | Price filter (defaults 50 / 2,000) |
| **Send Telegram alert** | Tick to also push the top-15 BUY summary to Telegram |
| **🚀 Run Scan** | Runs all *scan* categories you ticked, once, sharing data across strategies. Disabled (with a hint) until logged in |
| **🔒 Log out** | Re-locks the dashboard |

> Panels (Global Macro, Event-Driven) never enter the NSE engine. If you tick
> *only* panels, the sidebar tells you there's nothing to scan.

### 7.2 Category tabs

One tab per active category. Scan tabs show results of the **last Run Scan**
(if the last scan didn't include that category, the tab asks you to re-run).

### 7.3 Reading scan results

Top of each scan tab — five cards:

| Card | Meaning |
|---|---|
| **Scanned** | stocks evaluated |
| **BUY** / **SELL** / **HOLD** | signal split |
| **Scan Time** | when the run finished |

Then (per strategy, if a category has several you get **🔥 sub-tabs**):

**🚀 BUY Signals table** — "top N of M":

| Column | Meaning |
|---|---|
| **Ticker** | NSE symbol |
| **Price** | last close (₹) |
| **Indicators** | the strategy's readable metric string (e.g., BB width, RSI values) |
| **1W %** | price change over the last week |
| **Hist sigs** | how many times this setup fired in the scanned history |
| **Avg 4W fwd** | average *forward* 4-week return after those past signals — **historical, not a promise** |
| **Past signal dates** | when those signals occurred (check recency yourself) |
| **Zone break / Dir / Stop (zone)** | Hilega Milega extras: zone-breakout happened?, direction, zone-stop level |
| **EMA≈RSI warn** | profit-taking warning (EMA within 1 point of RSI) |

- **📥 Download BUY signals (CSV)** exports the visible table.
- **🗂 expander**: *All N evaluated stocks (incl. HOLD/SELL)* — full audit of
  the run.
- *"⚪ No BUY signals in this scan"* — signals only confirm **on bar
  closes**; try a later run rather than assuming a bug.

### 7.4 Scan History & Stats (bottom of the page)

Pick a strategy in **Stats for**:

| Metric | Meaning |
|---|---|
| **Scans** | total runs stored |
| **BUY rows** | BUY signals recorded |
| **Evaluated BUYs** | BUYs old enough to have a measurable 4-week outcome |
| **Avg 4W fwd** | average forward 4-week return of those evaluated signals |
| **Win rate** | % of evaluated signals with a positive outcome |
| **Last scan** | timestamp |

Then **history table** (last 200 rows) with filter **BUY only / All**.

> Storage keeps the **2 most recent scans per strategy** (older pruned) and
> lives on the server's ephemeral disk — **export CSV before a redeploy** if
> you need to keep data (§13.3).

---

## 8. Category & strategy reference

### 8.1 📈 Long Term — Hilega Milega (weekly)

*Trend-momentum on weekly bars; currently in a **2-week live trial** — treat
its signals as a tracked experiment (rule: freeze logic until the trial
review).*

**Indicators:** RSI-9 with the 50 midline · **WMA-21 of RSI** ("red line") ·
**EMA-3 of WMA** ("green line").

| Signal | Rule |
|---|---|
| **BUY** | RSI > 50 **and** WMA > 50 **and** close > previous week's high |
| **SELL** | RSI < 50 **and** WMA < 50 **and** close < previous week's low |

**Column extras:** historical signal count + avg 4-week forward return ·
zone-breakout stop level (low of the candle where RSI left the zone, longs;
high for shorts) · profit-taking warning when EMA touches RSI.

> ⚠️ The "70–75% accuracy" sometimes quoted for this setup is the original
> trader's own estimate — **unverified**. Use the stats cards instead.

### 8.2 🚀 Breakout / BTST — the squeeze test-bench (daily)

One idea, five variants: **Bollinger compression ("squeeze") → expansion
breakout.** They share ONE exit rule — *close back below the band mid after a
recent squeeze* — so you can compare ENTRY styles directly:

| Strategy | Entry idea | Use it for |
|---|---|---|
| **Volcano Watch** | squeeze *forming* (fast BB(10) windows), before the break | advance watchlist — prepare |
| **Volcano Blast** | squeeze → close > upper band **+ 1.5× volume** | the textbook breakout |
| **Volcano Snap** | BB(10) squeeze + **1.2× volume** on 4–6 session coils | faster, tighter coils |
| **Coil Break** | Bollinger *inside* Keltner squeeze → expansion trigger | classic BB⊆KC squeeze |
| **Darvas Box** | flat 20-bar box → close above box high | steady trend entries |

**Tip:** run Watch alone during the day; run the trigger variants after the
close (signals confirm on bar closes). All five can share one scan (data is
fetched once per stock).

### 8.3 ⚡ Intraday

| Strategy | Idea |
|---|---|
| **Volcano Blast 60** | Blast rules on **60-min bars** (note: Fyers caps intraday history at 100 days) |
| **Expiry Credit Spread (NIFTY / BANKNIFTY)** | expiry-day index-option credit spreads — full playbook in **§9** |

---

## 9. Expiry Credit Spread playbook

*Designed for someone **new to F&O**: defined risk, hard time discipline,
paper-trading first (see §15).*

### 9.1 When it's active

| | |
|---|---|
| **NIFTY expiry** | **every Tuesday** (since 01-Sep-2025; on an exchange holiday it shifts to the previous trading day) |
| **BANKNIFTY expiry** | **last Tuesday of the month** only (weeklies discontinued 20-Nov-2024) |
| **Entry window** | **09:30–11:30 IST** on expiry day only |
| **Forced exit** | **14:30 IST — always, no exceptions, never overnight** |

The **D-1 reminder** (§10) warns you the day before every expiry, listing
which indexes expire and repeating these numbers.

### 9.2 The setup

- **Bull Put** (uptrend): sell ATM+1% put, buy a further OTM put below it.
- **Bear Call** (downtrend): sell ATM+1% call, buy a further OTM call above.
- Strikes snap to **₹50 steps (NIFTY)** / **₹100 (BANKNIFTY)** from spot.
- **Entry filters:** in window ∧ ADX ≥ 20 (trend) ∧ price reclaims VWAP for
  2 candles ∧ volume > 1.0× its 20-period average.
- **Risk:** stop = **1.5 × ATR(14)** · target credit 35% of width ·
  max loss 2% of capital per spread · max **2** concurrent spreads.
- **Lots:** NIFTY **65** · BANKNIFTY **30** (SEBI revision, eff. Jan-2026).

### 9.3 Discipline checklist (expiry day)

```
☐ Read the D-1 reminder the evening/morning before
☐ Funds ready before 09:30 — entries only in the window
☐ No entry after 11:30 even if it looks perfect
☐ 14:30 — close everything, regardless of P&L
☐ Nothing carried overnight (this is a hard system rule)
☐ Log every paper trade before risking real money
```

> **Before any capital:** the historical backtests must be re-run on the
> **Tuesday regime** (older Thursday-based results are invalid), and a
> paper-trial period completed. This is a standing requirement while you're
> new to F&O.

---

## 10. Telegram alerts reference

Messages arrive in your configured Telegram chat from your bot.

| When (IST) | Message | What to do with it |
|---|---|---|
| **09:00 daily** | **SOD**: US close (S&P 500, Nasdaq 100, Dow, Russell) + ETF trade signals (±1% rule, ₹10k each) + NIFTY *previous* session O/H/L/C + **⚠️ D-1 expiry reminder when tomorrow is an expiry** | Plan the day: note signals for the 09:15 ETF entry; on Monday before expiry, read the reminder |
| **15:45 daily** | **EOD**: NIFTY today's open/high/low/close + % vs prior close | Journal the session. On weekends/holidays: "no session today" — expected |
| **On Run Scan** (if ticked) | Top **15 BUY setups** of the scan | Triage candidates; open the dashboard for the full table |

**Anatomy of the expiry reminder:**

```
⚠️ TOMORROW IS EXPIRY DAY — Tue 29-Sep-2026
   • NIFTY — weekly + monthly
   • BANKNIFTY — monthly
   Expiry Credit Spread plan:
     Entry window 09:30-11:30 IST | Forced exit 14:30 IST (no overnight)
     Lots: NIFTY 65 | BANKNIFTY 30
   Note: if the expiry day is an exchange holiday,
   NSE shifts expiry to the previous trading day.
```

**Rules the scheduler keeps:** at most **one message per job per day**
(state file), a **60-minute catch-up window** (an app restart between
09:00–10:00 still delivers), and **failed sends retry** until the window
closes. Weekend SOD/EOD messages still go out ("everyday" mode).

**If alerts stop arriving:** the app is probably asleep or unvisited — see
troubleshooting §12 (rows A1–A3).

---

## 11. Mobile tips

- **Start with Nifty 50 (fast)** — ~30 s on cellular; run *Full NSE* on Wi-Fi.
- **Copy-paste the redirect URL** is the trickiest step — practise once
  (§6.2); the code lives only ~2 minutes.
- Keep the dashboard **tab open** during market hours — the app stays "live"
  for scheduled jobs (one visit/unlock per server restart starts them).
- Landscape orientation widens the results table; horizontal scroll works.
- Use **📥 CSV** to open results in a spreadsheet app offline.
- The **panels** (Global Macro, Event-Driven) need no NSE scan — they're the
  fastest things to open on a weak connection.

---

## 12. Troubleshooting

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | "Dashboard is locked: DASHBOARD_PASSWORD is not configured" | Cloud Secrets empty/not saved | Paste secrets (Cloud ⋮ → Settings → Secrets) → Save → refresh (§13.2) |
| 2 | Password rejected | typo / wrong value | Use the exact `DASHBOARD_PASSWORD` value from secrets |
| 3 | 🔐 box every morning | token expired ~06:30 | normal — redo §6.2 |
| 4 | "auth_code expires ~2 min…" | slow copy | redo the login quickly |
| 5 | "Invalid auth_code" | partial/reused code | copy full URL; codes are single-use |
| 6 | 🚀 Run Scan greyed out | not logged in | complete the 🔓 box first |
| 7 | Scan slow / spinner long | Full NSE on mobile data | switch Universe to Nifty 50; full runs ~2–4 min by design |
| 8 | "⚪ No BUY signals" | setups confirm on bar closes | try later/next close — not an error |
| 9 | Tab says "last scan didn't include this category" | scan ran with fewer categories | re-run with that category ticked |
| 10 | **No Telegram messages at all** | app asleep (12 h idle) or no visit since redeploy | open+unlock the app once; confirm UptimeRobot ping is active |
| 11 | Messages arrive twice | two senders (local + cloud) running | keep only the cloud sender; local runs with `SCREENER_SCHEDULER_DISABLE=1` |
| 12 | 15:45 says "no session today" | weekend/holiday | expected |
| 13 | US section missing from 09:00 message | provider throttle | fallback chain usually recovers next day; check §13.4 |
| 14 | ETF quotes show "No Fyers token" | logged out | §6.2 |
| 15 | IPO panel warns about Apify token | `APIFY_TOKEN` missing in secrets | add in secrets (owner) → Save |
| 16 | IPO listed data looks old | Apify lags 2–4 weeks for listed IPOs | verify on NSE/BSE (the panel says this too) |
| 17 | App shows a build/error page | redeploy in progress or code error | wait 2–5 min; if persists, check Cloud logs (§13.5) or report with a screenshot |
| 18 | History/stats empty after deploy | `data/` resets on every redeploy | expected; export CSV beforehand next time |

**Reporting a bug:** screenshot + approximate time (IST) + which flow
(login/scan/panel/alert). The maintainer loop is: fix on laptop →
`git push` → app redeploys in 2–5 min → refresh your phone.

---

## 13. Operations & maintenance

### 13.1 Keep the app awake (needed for scheduled alerts)

Community Cloud **sleeps after ~12 h idle**. Free fix — an external monitor:

1. **uptimerobot.com** → *Add Monitor* → type **HTTP**.
2. URL = `https://stockscreener2026.streamlit.app` → save (10-min interval).
3. Verify status shows *Up*.

The ping keeps the *container* awake; your daily visit (login) is what
actually starts app code — do both and alerts fire reliably.

### 13.2 Where secrets live

| Secret | Local file | Cloud |
|---|---|---|
| Fyers client/secret/redirect, Telegram, Apify, Finnhub, dashboard password | `.streamlit/secrets.toml` | ⋮ → Settings → Secrets (encrypted) |

Rules: **secrets never in git, never in chat.** Keep both copies in sync —
after editing one, edit the other. Password changes propagate on Save.

### 13.3 Updates & data retention

- **Updating code** (maintainer): laptop → `git add/commit/push` → auto
  redeploy 2–5 min → phone refresh. **After every redeploy:** `data/`
  resets → do the Fyers login again; at most one duplicate scheduled message.
- **Export** history/CSV before a redeploy if you need to keep data.
- **Rollback:** `git revert` + push (same pipeline).

### 13.4 Daily/weekly checklist for the owner

```
DAILY    ☐ open + unlock app (starts scheduler)   ☐ Fyers login
         ☐ run desired scans                       ☐ skim 09:00/15:45 messages
WEEKLY   ☐ UptimeRobot monitor shows "Up"
         ☐ no duplicate messages this week
         ☐ git pull/push of fixes as needed
MONTHLY  ☐ review strategy stats (§7.4)
         ☐ rotate any secret exposed in screenshares (runbook §6)
```

### 13.5 Logins & diagnostics

- App logs: Streamlit Cloud → app page → **⋮ → Logs** (build + runtime).
- Local dev (maintainer): `streamlit run app.py` — but **disable the
  scheduler** while the cloud is the sender, otherwise messages duplicate.
- CLI scanning: `python engine.py -s <keys> -u nifty50` (see README).

---

## 14. FAQ

**Q — Do I need the laptop at all?**
No: phone can do login, scans, panels, alerts. The laptop is for *changing
code*.

**Q — Is my broker data safe?**
Yes. Your auth_code is pasted only into your own dashboard and exchanged
server-side; the token sits on the server disk and is never displayed. No
secrets live in the repo or chat.

**Q — Why does the login box appear every morning?**
Fyers tokens expire ~06:30 IST daily. It's a broker rule; the refresh-token
flow (15-day validity) is on the roadmap.

**Q — Why Tuesday? What about Thursday?**
NSE moved index-derivatives expiry to **Tuesday effective 01-Sep-2025**
(BSE keeps Thursday/Sensex day). The app implements the verified rules — see
§9.1.

**Q — Why did the reminder say "monthly" only for BANKNIFTY?**
Weekly BANKNIFTY options were discontinued on 20-Nov-2024.

**Q — Are the stats predictions?**
No — historical outcomes of past signals (4-week horizon). Markets change.

**Q — Can two people use it?**
Designed for one owner. The gate allows shared use, but alerts are sent once
per day to the configured chat, and only one *instance* may send them.

**Q — Why is my scan history gone?**
Cloud disk is ephemeral — every redeploy resets `data/`.

**Q — Does it trade by itself?**
No. Research and alerts only; orders are never placed.

**Q — Cost?**
₹0 — Community Cloud free tier, free keep-alive, free Telegram bot.

---

## 15. Safety & responsible use

1. **Not investment advice** — signals are candidates for review; you decide.
2. **Paper first.** The Expiry Credit Spread requires re-run backtests
   (Tuesday regime) + a paper-trial period before any real capital — you're
   new to F&O.
3. **Respect the time stops** — 11:30 last entry, 14:30 forced exit, never
   overnight (§9.3).
4. **Position limits are built in** (2% capital risk/spread, max 2 spreads,
   ₹10k per ETF trade) — don't override them manually.
5. **Hilega Milega is in a live trial** — its logic is frozen until the
   trial review; log its signals faithfully.
6. **Verify expiry dates independently** on NSE on unusual days (holidays
   shift expiry) — the reminder itself carries this caveat.

---

## 16. Glossary

| Term | Meaning |
|---|---|
| **IST** | Indian Standard Time = UTC+5:30 (no daylight saving) |
| **SOD / EOD** | Start/End of Day — the 09:00 / 15:45 IST messages |
| **D-1** | One day before an event (e.g., D-1 expiry reminder) |
| **Expiry day** | Last trading day of an options contract; NSE index expiry = Tuesday since Sep-2025 |
| **Lot** | Minimum options contract size — NIFTY 65, BANKNIFTY 30 (Jan-2026) |
| **Credit spread** | Sell one option, buy a farther strike → collect premium with defined risk |
| **OTM / ATM** | Out-of-the-money / at-the-money strike vs current index price |
| **VWAP** | Volume-weighted average price — intraday trend anchor |
| **ADX** | Trend-strength indicator (≥20 = trending) |
| **ATR** | Average True Range — volatility size (used for the 1.5× stop) |
| **BB / squeeze** | Bollinger Bands; "squeeze" = bands compressing before expansion |
| **Keltner (KC)** | Volatility channel; BB-inside-KC = classic squeeze (Coil Break) |
| **Darvas box** | A flat price box used to enter on breakouts above it |
| **auth_code** | One-time ~2-minute Fyers authorization code in the redirect URL |
| **Universe** | Stock set scanned: Nifty 50 or full NSE equity list |
| **Keep-alive** | Free external ping that prevents idle sleep of the cloud app |
| **Redeploy** | App rebuild triggered by a git push (resets `data/`) |
| **Regime** | A historical rule period for expiry logic (Thursday-era → transition → Tuesday-era) |

---

## Appendix A — Maintainer quick map

*(For whoever extends the app; full detail in the TDD/README.)*

| Want to… | Go to |
|---|---|
| Add a strategy | new file in `strategies/` + `@register_strategy` in `strategies/registry.py` (README has a template) |
| Change category layout | `CATEGORIES` dict in `app.py` |
| Change thresholds/universe | `config/settings.py` |
| Change alert times | `JOBS` in `core/scheduler.py` (IST) |
| Change expiry/lot logic | `strategies/expiry_credit_spread.py` (regime constants) |
| Change ETF signals/capital | `strategies/global_macro/config.py` |
| Rotate secrets | `.streamlit/secrets.toml` **and** Cloud Secrets (runbook §6) |
| Run tests | cloud checks / scheduler checks / expiry checks (see TDD §8) |
