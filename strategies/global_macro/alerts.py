# Telegram Alerts for Index Follower
# =================================

import requests
from datetime import datetime
from config.keys import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def send_telegram(msg: str) -> bool:
    """Send Telegram message."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown",
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def format_sod_telegram(plan: Dict) -> str:
    """Format SOD plan for Telegram (9:00 AM)."""
    lines = [
        "INDEX FOLLOWER - SOD PLAN (9:00 AM)",
        f"Date: {plan['date']}",
        "-" * 30,
        "US CLOSE (Prev Day):"
    ]
    
    for idx, pct in plan["us_indices"].items():
        emoji = "GREEN" if pct and pct > 0 else ("RED" if pct and pct < 0 else "GRAY")
        lines.append(f"   {emoji} {idx}: {pct:+.2f}%")
    
    if plan["signals"]:
        lines.append("\nTRADE SIGNALS:")
        for s in plan["signals"]:
            lines.append(f"   {s['direction']} {s['etf_symbol']} -- {s['trigger']} | Rs.{s['capital']:,}")
    
    if plan["major_alerts"]:
        lines.append("\nMAJOR MOVES:")
        for a in plan["major_alerts"]:
            lines.append(f"   {a['message']}")
    
    if not plan["signals"]:
        lines.append("\nNo signals today (threshold not met)")
    
    lines.append("\nCapital: Rs.10,000 per trade | Exit: User discretion")
    return "\n".join(lines)


def format_eod_telegram(trades_today: List) -> str:
    lines = [
        "INDEX FOLLOWER - EOD SUMMARY (3:45 PM)",
        f"Date: {datetime.now().strftime('%Y-%m-%d')}",
        "-" * 30,
    ]
    
    if trades_today:
        lines.append(f"\nTRADES TODAY ({len(trades_today)}):")
        total_pnl = 0
        for t in trades_today:
            pnl = t.get("pnl", 0)
            lines.append(f"   {t['direction']} {t['etf']} | Entry: Rs.{t['entry']:.2f} | Exit: Rs.{t['exit']:.2f} | P&L: Rs.{pnl:+,.0f}")
            total_pnl += pnl
        lines.append(f"\nNet P&L: Rs.{total_pnl:+,.0f}")
    else:
        lines.append("\nNo trades executed today")
    
    lines.append("\nSee you tomorrow at 9:00 AM!")
    return "\n".join(lines)


def send_telegram(msg: str) -> bool:
    """Send Telegram message."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown",
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def send_sod_alert(index_follower) -> bool:
    """Send SOD plan at 9:00 AM IST."""
    plan = index_follower.get_sod_plan()
    msg = format_sod_telegram(plan)
    return send_telegram(msg)


def send_eod_alert(trades_today: List) -> bool:
    msg = format_eod_summary(trades_today)
    return send_telegram(msg)


def format_sod_telegram(plan: Dict) -> str:
    lines = [
        "INDEX FOLLOWER - SOD PLAN (9:00 AM)",
        f"Date: {plan['date']}",
        "-" * 30,
        "US CLOSE (Prev Day):"
    ]
    
    for idx, pct in plan["us_indices"].items():
        emoji = "GREEN" if pct and pct > 0 else ("RED" if pct and pct < 0 else "GRAY")
        lines.append(f"   {emoji} {idx}: {pct:+.2f}%")
    
    if plan["signals"]:
        lines.append("\nTRADE SIGNALS:")
        for s in plan["signals"]:
            lines.append(f"   {s['direction']} {s['etf_symbol']} -- {s['trigger']} | Rs.{s['capital']:,}")
    
    if plan["major_alerts"]:
        lines.append("\nMAJOR MOVES:")
        for a in plan["major_alerts"]:
            lines.append(f"   {a['message']}")
    
    if not plan["signals"]:
        lines.append("\nNo signals today (threshold not met)")
    
    lines.append("\nCapital: Rs.10,000 per trade | Exit: User discretion")
    return "\n".join(lines)


def format_eod_summary(trades_today: List) -> str:
    lines = [
        "INDEX FOLLOWER - EOD SUMMARY (3:45 PM)",
        f"Date: {datetime.now().strftime('%Y-%m-%d')}",
        "-" * 30,
    ]
    
    if trades_today:
        lines.append(f"\nTRADES TODAY ({len(trades_today)}):")
        total_pnl = 0
        for t in trades_today:
            pnl = t.get("pnl", 0)
            lines.append(f"   {t['direction']} {t['etf']} | Entry: Rs.{t['entry']:.2f} | Exit: Rs.{t['exit']:.2f} | P&L: Rs.{pnl:+,.0f}")
            total_pnl += pnl
        lines.append(f"\nNet P&L: Rs.{total_pnl:+,.0f}")
    else:
        lines.append("\nNo trades executed today")
    
    lines.append("\nSee you tomorrow at 9:00 AM!")
    return "\n".join(lines)


def send_telegram(msg: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown",
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def send_sod_alert(index_follower) -> bool:
    plan = index_follower.get_sod_plan()
    msg = format_sod_telegram(plan)
    return send_telegram(msg)


def send_eod_alert(trades_today: List) -> bool:
    msg = format_eod_summary(trades_today)
    return send_telegram(msg)