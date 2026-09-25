"""
Scheduled Telegram notifications
================================
All times IST (fixed UTC+5:30 — India has no DST).

  09:00 IST  SOD — US close + signals (Index Follower plan), NIFTY previous
                   session open/close summary, and a ONE-DAY-PRIOR expiry
                   reminder when tomorrow is a NIFTY/BANKNIFTY expiry day.
  15:45 IST  EOD — NIFTY today open/high/low/close summary.

Each job runs at most once per day (state: data/scheduler_state.json) with
a 60-minute catch-up window, so restarting the app shortly after the
scheduled time still delivers the message.  Failed sends are retried on
the next 30s tick until the window closes.

Runs automatically inside the dashboard process — start_scheduler() is
idempotent — or standalone, without Streamlit:

    python core/scheduler.py

On Streamlit Community Cloud the app must stay awake (see the keep-alive
ping in CLOUD_SETUP.md) for jobs to fire.  Run only ONE instance (dashboard
or standalone) or messages will duplicate.

Set SCREENER_SCHEDULER_DISABLE=1 to suppress all sends (used by tests).
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Callable, Dict, List, Optional

# Add project root to path (standalone: python core/scheduler.py)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from strategies.global_macro.alerts import send_telegram

IST = timezone(timedelta(hours=5, minutes=30))

JOBS: Dict[str, dtime] = {
    "sod_summary": dtime(9, 0),
    "eod_summary": dtime(15, 45),
}
GRACE_MINUTES = 60          # catch-up window after the scheduled time
TICK_SECONDS = 30

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "data", "scheduler_state.json")


# ---------------------------------------------------------------------------
# Due logic / state
# ---------------------------------------------------------------------------

def _due(sched: dtime, now: datetime, last_sent: Optional[str]) -> bool:
    """True when `sched` has passed today (within grace) and not yet sent."""
    if last_sent == now.date().isoformat():
        return False
    sched_dt = datetime.combine(now.date(), sched, tzinfo=now.tzinfo)
    if now < sched_dt:
        return False
    if now > sched_dt + timedelta(minutes=GRACE_MINUTES):
        return False
    return True


def _load_state(state_path: str) -> dict:
    try:
        with open(state_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(state: dict, state_path: str) -> None:
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except Exception as exc:
        print(f"[scheduler] state save failed: {exc}")


# ---------------------------------------------------------------------------
# NIFTY session summaries (yfinance — no Fyers token needed)
# ---------------------------------------------------------------------------

def _yf_flat(symbol: str, **kwargs) -> pd.DataFrame:
    """yfinance download with MultiIndex columns flattened (single ticker)."""
    import yfinance as yf
    df = yf.download(symbol, progress=False, auto_adjust=False, **kwargs)
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def nifty_prev_session_section(now: datetime) -> str:
    """Previous completed NIFTY session: open/high/low/close + % change."""
    try:
        df = _yf_flat("^NSEI", period="10d", interval="1d")
        if df.empty:
            return "NIFTY: session data unavailable"
        df = df[df.index.date < now.date()]
        if len(df) < 2:
            return "NIFTY: session data unavailable"
        prev, before = df.iloc[-1], df.iloc[-2]
        pct = (prev["Close"] - before["Close"]) / before["Close"] * 100
        tag = "UP" if pct >= 0 else "DOWN"
        day = df.index[-1].strftime("%a %d-%b")
        return (
            f"NIFTY (prev session {day}): [{tag}]\n"
            f"   O {prev['Open']:,.1f}  H {prev['High']:,.1f}  "
            f"L {prev['Low']:,.1f}  C {prev['Close']:,.1f}  ({pct:+.2f}%)"
        )
    except Exception as exc:
        return f"NIFTY: session summary unavailable ({exc})"


def nifty_today_section(now: datetime) -> str:
    """Today's NIFTY session from hourly bars (valid from ~15:30 IST)."""
    try:
        today = now.date()
        df = _yf_flat("^NSEI", period="5d", interval="1h")
        if df.empty:
            return "NIFTY: today's session data unavailable"
        day = df[df.index.date == today]
        if day.empty:
            return f"NIFTY: no session today ({today.strftime('%a %d-%b')}) — holiday?"
        daily = _yf_flat("^NSEI", period="10d", interval="1d")
        prev_close = None
        if not daily.empty:
            done = daily[daily.index.date < today]
            if len(done):
                prev_close = float(done.iloc[-1]["Close"])
        o, c = float(day.iloc[0]["Open"]), float(day.iloc[-1]["Close"])
        hi, lo = float(day["High"].max()), float(day["Low"].min())
        pct = f"  ({(c - prev_close) / prev_close * 100:+.2f}%)" if prev_close else ""
        return (
            f"NIFTY TODAY ({today.strftime('%a %d-%b')}):\n"
            f"   O {o:,.1f}  H {hi:,.1f}  L {lo:,.1f}  C {c:,.1f}{pct}"
        )
    except Exception as exc:
        return f"NIFTY: today's summary unavailable ({exc})"


# ---------------------------------------------------------------------------
# Expiry reminder (ONE day prior)
# ---------------------------------------------------------------------------

def expiry_reminder(now: datetime) -> Optional[str]:
    """Reminder text when TOMORROW is a NIFTY/BANKNIFTY expiry day, else None.

    Weekday-based (regime-aware via strategies.expiry_credit_spread).
    """
    from strategies.expiry_credit_spread import is_expiry_day

    tmrw = pd.Timestamp(now.date() + timedelta(days=1))
    hits: List[str] = []
    if is_expiry_day(tmrw, "NIFTY"):
        monthly = (tmrw + pd.Timedelta(days=7)).month != tmrw.month
        hits.append(f"NIFTY — weekly{' + monthly' if monthly else ''}")
    if is_expiry_day(tmrw, "BANKNIFTY"):
        hits.append("BANKNIFTY — monthly")
    if not hits:
        return None

    # Lots from the registry (single source of truth) with safe fallback
    n_lot, b_lot = 65, 30
    try:
        from strategies.registry import get_registry
        reg = get_registry()
        n_lot = reg["expiry_credit_spread_nifty"]["strategy_obj"]._config.lot_size
        b_lot = reg["expiry_credit_spread_banknifty"]["strategy_obj"]._config.lot_size
    except Exception:
        pass

    lines = [f"⚠️ TOMORROW IS EXPIRY DAY — {tmrw.strftime('%a %d-%b-%Y')}"]
    lines += [f"   • {h}" for h in hits]
    lines += [
        "   Expiry Credit Spread plan:",
        "     Entry window 09:30-11:30 IST | Forced exit 14:30 IST (no overnight)",
        f"     Lots: NIFTY {n_lot} | BANKNIFTY {b_lot}",
        "   Note: if the expiry day is an exchange holiday,",
        "   NSE shifts expiry to the previous trading day.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def build_sod_message(now: datetime) -> str:
    """09:00 IST: US close + signals + NIFTY prev session + expiry reminder."""
    blocks: List[str] = []
    try:
        from strategies.global_macro.index_follower import IndexFollower
        idx = IndexFollower()
        blocks.append(idx.format_sod_telegram(idx.get_sod_plan()))
    except Exception as exc:
        blocks.append(f"US close data unavailable: {exc}")

    blocks.append(nifty_prev_session_section(now))
    reminder = expiry_reminder(now)
    if reminder:
        blocks.append(reminder)
    return "\n\n".join(blocks)


def build_eod_message(now: datetime) -> str:
    """15:45 IST: NIFTY open/high/low/close for today."""
    return nifty_today_section(now)


JOBS_FN: Dict[str, Callable[[datetime, Callable[[str], bool]], bool]] = {
    "sod_summary": lambda now, sender: bool(sender(build_sod_message(now))),
    "eod_summary": lambda now, sender: bool(sender(build_eod_message(now))),
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_due_jobs(
    now: datetime,
    state_path: str = STATE_FILE,
    sender: Callable[[str], bool] = send_telegram,
) -> List[str]:
    """Run every job due at `now`; returns the ids actually sent."""
    state = _load_state(state_path)
    sent: List[str] = []
    for job, sched in JOBS.items():
        if not _due(sched, now, state.get(job)):
            continue
        try:
            ok = JOBS_FN[job](now, sender)
        except Exception as exc:
            print(f"[scheduler] {job} failed: {exc}")
            ok = False
        if ok:
            state[job] = now.date().isoformat()
            _save_state(state, state_path)
            sent.append(job)
            print(f"[scheduler] sent {job} for {now.date()}")
    return sent


def _loop() -> None:
    print(f"[scheduler] started (IST jobs: "
          f"{', '.join(f'{j} {t.strftime('%H:%M')}' for j, t in JOBS.items())})")
    while True:
        time.sleep(TICK_SECONDS)
        try:
            run_due_jobs(datetime.now(IST))
        except Exception as exc:  # never let the thread die
            print(f"[scheduler] tick error: {exc}")


_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def start_scheduler() -> bool:
    """Start the background job thread once per process (idempotent)."""
    global _thread
    if os.environ.get("SCREENER_SCHEDULER_DISABLE"):
        print("[scheduler] disabled via SCREENER_SCHEDULER_DISABLE")
        return False
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        _thread = threading.Thread(target=_loop, name="screener-scheduler", daemon=True)
        _thread.start()
    return True


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("Screener Telegram scheduler (Ctrl+C to stop)")
    start_scheduler()
    while True:
        time.sleep(60)
