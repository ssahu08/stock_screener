import datetime
import os
import sys
import time
import duckdb
import pandas as pd
import requests
import yfinance as yf

# Resolve project root for imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)

from config.keys import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
DB_PATH = os.path.join(PROJECT_ROOT, "data", "screener.duckdb")

def send_telegram_alert(message: str):
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as err:
        print(f"Failed to send Telegram alert: {err}")

def execute_fundamental_scan(choice: str):
    print("\n" + "=" * 55)
    print("🔍 INITIALIZING FUNDAMENTAL FILTER ENGINE")
    print("=" * 55)

    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at {DB_PATH}. Run screener_engine.py first.")
        return

    # 1. Fetch Latest Technical BUY Signals
    con = duckdb.connect(DB_PATH)
    try:
        latest_scan = con.execute("SELECT scan_id, strategy_name FROM multi_strategy_scans ORDER BY scan_time DESC LIMIT 1").fetchone()
        if not latest_scan:
            print("❌ No scans found in database.")
            return

        scan_id, strat_name = latest_scan[0], latest_scan[1]
        query = f"SELECT ticker, current_price, key_metrics FROM multi_strategy_scans WHERE scan_id = '{scan_id}' AND signal = 'BUY'"
        buys_df = con.execute(query).df()
    except Exception as e:
        print(f"❌ Database error: {e}")
        return
    finally:
        con.close()

    if buys_df.empty:
        print(f"⚪ No BUY signals to evaluate from scan: {scan_id}")
        return

    # 2. Strategy Mapping
    if choice == "1":
        strat_title = "QUALITY COMPOUNDERS (ROE >15%, Debt <0.5)"
    elif choice == "2":
        strat_title = "PEAD MOMENTUM (Qtr Earnings >20%, Near 52W High)"
    elif choice == "3":
        strat_title = "DEEP VALUE (P/E < 15, P/B < 2)"

    print(f"🎯 Evaluating {len(buys_df)} technical setups against: {strat_title}")
    print("⏳ Fetching deep financials via Yahoo Finance...")
    print("-" * 55)

    elite_stocks = []

    # 3. Yahoo Finance Loop
    for _, row in buys_df.iterrows():
        ticker = row['ticker']
        price = row['current_price']
        
        try:
            stock = yf.Ticker(f"{ticker}.NS")
            info = stock.info

            # Extract broad metrics needed across all strategies
            roe = info.get('returnOnEquity', 0) or 0
            debt_raw = info.get('debtToEquity', 0) or 0
            debt_eq = debt_raw / 100.0
            pe_ratio = info.get('trailingPE', 0) or 999
            pb_ratio = info.get('priceToBook', 0) or 999
            qtr_earnings_growth = info.get('earningsQuarterlyGrowth', 0) or 0
            high_52w = info.get('fiftyTwoWeekHigh', 0) or price
            
            passed = False
            reason = ""

            # APPLY CHOSEN STRATEGY LOGIC
            if choice == "1": # Quality Compounder
                if roe >= 0.15 and debt_eq <= 0.5:
                    passed = True
                    reason = f"ROE: {round(roe*100,1)}% | D/E: {round(debt_eq,2)}"
            
            elif choice == "2": # PEAD Drift
                # Earnings grew > 20% YoY, AND price is within 15% of 52-week high
                if qtr_earnings_growth >= 0.20 and price >= (high_52w * 0.85):
                    passed = True
                    reason = f"EPS Grwth: {round(qtr_earnings_growth*100,1)}% | 52wH: ₹{high_52w}"
            
            elif choice == "3": # Deep Value
                if pe_ratio > 0 and pe_ratio < 15 and pb_ratio > 0 and pb_ratio < 2:
                    passed = True
                    reason = f"P/E: {round(pe_ratio,1)} | P/B: {round(pb_ratio,1)}"

            # Results tracking
            if passed:
                print(f"🏆 PASS: {ticker.ljust(12)} | {reason}")
                elite_stocks.append({
                    "ticker": ticker,
                    "price": price,
                    "tech_metrics": row['key_metrics'],
                    "reason": reason
                })
            else:
                pass # Silently skip failures to keep console clean

        except Exception:
            print(f"⚠️ Skipped {ticker}: Yahoo Finance data error")

        time.sleep(0.3)  # Rate limit politeness

    # 4. Display & Alert
    print("\n" + "=" * 55)
    print(f"✨ {strat_title} SELECTIONS: {len(elite_stocks)}")
    print("=" * 55)

    if elite_stocks:
        summary_df = pd.DataFrame(elite_stocks)
        print(summary_df.to_string(index=False))

        msg = f"🏆 *TECHNO-FUNDAMENTAL ALERT*\n"
        msg += f"🔎 *Filter:* `{strat_title}`\n"
        msg += f"──────────────────────\n"
        for s in elite_stocks:
            msg += (f"• *{s['ticker']}* | ₹{s['price']}\n"
                    f"  {s['reason']}\n"
                    f"  Tech: `{s['tech_metrics']}`\n\n")

        send_telegram_alert(msg)
        print("📲 Elite alert dispatched to Telegram.")
    else:
        print("⚪ No stocks passed the rigorous criteria this run.")

# ============================================================
# MAIN MENU
# ============================================================
if __name__ == "__main__":
    while True:
        print("\n" + "="*55)
        print("🏛️ FUNDAMENTAL ANALYSIS ENGINE MENU")
        print("="*55)
        print("1. Quality Compounders (High ROE, Low Debt)")
        print("2. PEAD Momentum (Post-Earnings Drift & Growth)")
        print("3. Deep Value (Low P/E, Low P/B)")
        print("4. Exit")
        
        choice = input("\nSelect fundamental filter (1/2/3/4): ").strip()
        
        if choice == "4":
            print("Exiting fundamental engine. Goodbye!")
            break
        elif choice in ["1", "2", "3"]:
            execute_fundamental_scan(choice)
        else:
            print("❌ Invalid selection. Please type 1, 2, 3, or 4.")