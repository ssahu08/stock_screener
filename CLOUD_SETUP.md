# Streamlit Community Cloud setup (mobile access)

Run the dashboard on your phone in the office — no laptop needed.
Free, ~15 minutes of one-time setup.

**Security model at a glance**

| Secret | Where it lives | In git? |
|---|---|---|
| Fyers CLIENT_ID / SECRET_KEY, Telegram, Apify, Finnhub | `.streamlit/secrets.toml` locally + Streamlit Cloud's encrypted Secrets store | ❌ never (and old copies were purged from git history) |
| `DASHBOARD_PASSWORD` | same secrets store | ❌ never |
| Daily Fyers access token | app server disk only (`data/`), expires ~6:30 AM IST | ❌ never, never displayed |
| `config/keys.py` | committed **loader** — reads secrets, contains none | ✅ safe |

---

## 1. One-time deployment

1. Sign up at **share.streamlit.io** with your GitHub account (`ssahu08`).
2. During onboarding choose *"Yes, I already have a repository"* →
   authorize Streamlit's GitHub App → grant it access to the **private**
   repo `ssahu08/stock_screener`.
3. **New app** → repository `stock_screener`, branch `main`,
   Main file path **`app.py`** → *Advanced settings* → Python **3.12**
   → *Deploy*. First build takes ~3–5 min (installs `requirements.txt`).
4. **Add secrets:** app page → **⋮ menu → Settings → Secrets** → paste the
   *entire contents* of your local `.streamlit/secrets.toml` (it already
   holds all 7 values + the password) → *Save* → app restarts.
   The file itself never leaves git-ignore; Streamlit stores it encrypted.
5. Open the app URL (e.g. `https://<name>.streamlitcloud.app`) →
   enter the dashboard password → you're in.

> Lost the password? It's the `DASHBOARD_PASSWORD` line in your local
> `.streamlit/secrets.toml`.

## 2. Daily Fyers login from your phone (~30 sec)

Tokens expire every day around **6:30 AM IST**, so each trading morning:

1. Open the dashboard → **🔓 Open Fyers Login** → phone browser opens the
   Fyers login page → log in.
2. You land on a redirect page — **copy the full URL from the address bar**
   (it contains `auth_code=`).
   - *Android Chrome:* tap the address bar → copy.
     If the URL hides, use **⋮ → Share → Copy link**.
   - *iPhone Safari:* tap the URL → *Copy*.
3. Back in the dashboard tab → **paste** into the box → **✅ Sign in**.

The auth_code lives ~2 minutes — copy it right after login. The code and
the token are exchanged **server-side inside your own dashboard**; neither
ever passes through chat, email, or git.

(On your laptop, `python core/auth.py` still works as before.)

## 3. Keep the app awake (needed for scheduled alerts)

Community Cloud **sleeps an app after ~12 h without visits**. Add a free
external monitor that opens your app URL every ~10 minutes:

- **UptimeRobot** (uptimerobot.com, free, 50 monitors / 5-min interval):
  Add Monitor → type *HTTP* → URL = your app URL → save.
- Or **cron-job.org**: HTTP GET every 10 minutes.

Opening the URL also wakes a sleeping app within seconds, so even without
the monitor you only lose convenience — not data.

While the app is awake, the built-in scheduler (`core/scheduler.py`) sends:
**09:00 IST** — US close + signals, NIFTY previous session open/close, and a
**one-day-prior expiry reminder** (NIFTY/BANKNIFTY); **15:45 IST** — NIFTY
open/high/low/close summary. State lives in `data/scheduler_state.json`
(resets on redeploy — at most one duplicate per job after a redeploy).

## 4. Updating the app (your laptop → phone workflow)

```bash
# on your laptop
git add -A
git commit -m "what changed"
git push
```

Streamlit Cloud **redeploys automatically** on push (~2–5 min build).
On your phone: just refresh the URL. Nothing to install, ever.

## 5. Known free-tier limits

- **Sleep after ~12 h idle** → fixed by the keep-alive ping above.
- **Ephemeral disk:** `data/` (DuckDB scan history, today's token, IPO
  verification cache) resets on every redeploy/restart — re-login after
  deploys; export scan history to CSV before a redeploy if you need it.
- **Server clock is UTC:** the "token valid for today" check uses the
  server's date; logging in after ~6 AM IST is always safe.
- **Rate limits:** yfinance/Finnhub from cloud IPs occasionally throttle —
  the fallback chain (Finnhub → TwelveData → yfinance) covers this.
- Full-NSE scans (~2–4 min) run fine; use *Nifty 50 (fast)* on mobile data.

## 6. If the repo is ever made public — rotate first

1. Fyers API app **SECRET_KEY** (api-dashboard.fyers.in → My apps)
2. Telegram **bot token** (@BotFather → /revoke) and chat id
3. **Apify** token, **Finnhub** key
4. Then replace the values in `.streamlit/secrets.toml` **and** in the
   Streamlit Cloud Secrets box.

`git log` was scrubbed of historical secrets (all commit SHAs changed in
the process — any old clone must be re-cloned, not pulled).
