"""
Telegram Alert Module
=====================

Sends scan results and alerts to a Telegram chat.

⚠️ CONFIGURATION:
Before using Telegram alerts:
  1. Get a bot token from @BotFather on Telegram
  2. Get your chat ID by messaging @userinfobot or @getidsbot
  3. Add to config/keys.py:
     TELEGRAM_BOT_TOKEN = "YOUR_BOT_TOKEN"
     TELEGRAM_CHAT_ID = "YOUR_CHAT_ID"

If these are not set (or set to placeholder values), the module
silently returns without sending — no crash, no error.

Token security:
- Token stored in config/keys.py (gitignored)
- Never sent outside this module
- Module exits gracefully if tokens not configured
"""

import requests
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.keys import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID


def send_telegram_alert(message: str) -> bool:
    """Send a message to the configured Telegram chat.
    
    Args:
        message: Text message to send (Markdown v2 supported)
    
    Returns:
        True if message sent successfully, False otherwise
    """
    # Check if Telegram is configured
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        # print("📢 Telegram not configured — skipping alert")  # Uncomment for debug
        return False
    
    if not TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID == "YOUR_TELEGRAM_CHAT_ID":
        # print("📢 Chat ID not configured — skipping alert")  # Uncomment for debug
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        response = requests.post(url, json=payload, timeout=15)
        
        if response.status_code == 200:
            # print("📲 Telegram alert sent successfully")  # Debug
            return True
        else:
            # print(f"❌ Telegram API error: {response.text}")  # Debug
            return False
            
    except Exception as e:
        # print(f"❌ Failed to send Telegram alert: {e}")  # Debug
        return False


def format_signal_alert(signal_data: dict) -> str:
    """Format a single signal into a nice Telegram message.
    
    Args:
        signal_data: Dict from strategy evaluation containing:
            - ticker: str (e.g. "RELIANCE")
            - current_price: float
            - rsi: float
            - wma: float
            - ema: float
            - forward_4w_ret: float
            - weekly_change: float
            - metrics: str
    
    Returns:
        Formatted Markdown string ready for Telegram
    """
    msg = (f"🚀 *BUY SIGNAL*:\n"
           f"• *Ticker:* `{signal_data['ticker']}`\n"
           f"• *Price:* ₹{signal_data['current_price']}\n"
           f"• *RSI:* {signal_data['rsi']}\n"
           f"• *WMA:* {signal_data['wma']}\n"
           f"• *EMA:* {signal_data['ema']}\n"
           f"• *4W Forward Return:* `{signal_data['forward_4w_ret']}%`\n"
           f"• *Weekly Change:* `{signal_data['weekly_change']}%`\n"
           f"• *Metrics:* {signal_data['metrics']}")
    
    return msg


def test_connection() -> bool:
    """Quick test to verify Telegram bot can connect.
    
    Returns:
        True if bot token is valid and API is reachable
    """
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            bot_info = response.json()
            # print(f"✅ Connected to Telegram bot: @{bot_info['result']['username']}")  # Debug
            return True
    except Exception:
        pass
    
    return False