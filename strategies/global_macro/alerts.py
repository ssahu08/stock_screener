# Telegram Alerts for Index Follower
# ===================================

from typing import Dict, List

import requests
from datetime import datetime

from config.keys import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def send_telegram(msg: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def format_sod_telegram(plan: Dict) -> str:
    """Format SOD plan for Telegram (9:00 AM IST)."""
    lines = [
        "INDEX FOLLOWER - SOD PLAN (9:00 AM)",
        f"Date: {plan['date']}",
        "-" * 30,
        "US CLOSE (Prev Day):",
    ]
    for idx, pct in plan["us_indices"].items():
        tag = "UP" if pct and pct > 0 else ("DOWN" if pct and pct < 0 else "FLAT")
        lines.append(f"   [{tag}] {idx}: {pct:+.2f}%")

    if plan["signals"]:
        lines.append("")
        lines.append("TRADE SIGNALS:")
        for s in plan["signals"]:
            lines.append(
                f"   {s['direction']} {s['etf_symbol']} -- {s['trigger']} "
                f"| Rs.{s['capital']:,}"
            )
    else:
        lines.append("")
        lines.append("No signals today (threshold not met)")

    if plan["major_alerts"]:
        lines.append("")
        lines.append("MAJOR MOVES:")
        for a in plan["major_alerts"]:
            lines.append(f"   {a['message']}")

    lines.append("")
    lines.append("Capital: Rs.10,000 per trade | Exit: User discretion")
    return "\n".join(lines)


def format_eod_summary(trades_today: List) -> str:
    """Format EOD summary for Telegram (3:45 PM IST)."""
    lines = [
        "INDEX FOLLOWER - EOD SUMMARY (3:45 PM)",
        f"Date: {datetime.now().strftime('%Y-%m-%d')}",
        "-" * 30,
    ]
    if trades_today:
        lines.append("")
        lines.append(f"TRADES TODAY ({len(trades_today)}):")
        total_pnl = 0
        for t in trades_today:
            pnl = t.get("pnl", 0)
            lines.append(
                f"   {t['direction']} {t['etf']} | Entry: Rs.{t['entry']:.2f} "
                f"| Exit: Rs.{t['exit']:.2f} | P&L: Rs.{pnl:+,.0f}"
            )
            total_pnl += pnl
        lines.append("")
        lines.append(f"Net P&L: Rs.{total_pnl:+,.0f}")
    else:
        lines.append("")
        lines.append("No trades executed today")
    return "\n".join(lines)


def send_sod_alert(index_follower) -> bool:
    """Send SOD plan at 9:00 AM IST."""
    plan = index_follower.get_sod_plan()
    return send_telegram(format_sod_telegram(plan))


def send_eod_alert(trades_today: List) -> bool:
    """Send EOD summary at 3:45 PM IST."""
    return send_telegram(format_eod_summary(trades_today))
