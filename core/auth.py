"""
Authenticator Module
====================

OPTION A: USER-RUN LOGIN
========================

This module enables the "Option A" workflow where the user runs
the login in their own terminal. The access token NEVER passes
through this chat or any shared environment.

Workflow:
  1. User runs:   python core/auth.py
  2. Browser opens  → user logs into Fyers
  3. User copies   → auth_code from browser URL
  4. User pastes   → auth_code into terminal prompt
  5. Token is saved to: data/fyers_token.txt  (local only)
  6. Token expires at 6:30 AM IST next day

⚠️ SECURITY:
- Token file stored ONLY on user's machine
- Never in this chat's permanent history
- .gitignore protects data/ from GitHub exposure
- Token auto-expires daily
"""

import sys
import os
import webbrowser
from datetime import date
from typing import Optional

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fyers_apiv3 import fyersModel
from config.keys import CLIENT_ID, SECRET_KEY, REDIRECT_URI


def authenticate_interactive() -> Optional[str]:
    """Run interactive Fyers authentication.
    
    This must be run in USER'S terminal. It:
    1. Opens browser to Fyers login page
    2. Asks user to paste auth_code from redirect URL
    3. Exchanges auth_code for access_token
    4. Saves token to data/fyers_token.txt
    
    Returns:
        access_token string if successful, None if cancelled
    """
    
    token_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "data", "fyers_token.txt"
    )
    token_path = os.path.normpath(token_path)
    
    # Check if we already have a valid token for today
    if os.path.exists(token_path):
        file_date = date.fromtimestamp(os.path.getmtime(token_path))
        if file_date == date.today():
            print("✅ Found valid cached token for today. Skipping login.")
            with open(token_path, "r") as f:
                token = f.read().strip()
            print("✅ Token loaded successfully")
            return token
    
    # Step 1: Generate auth URL
    print("=" * 60)
    print("🔐 FYERS AUTHENTICATION")
    print("=" * 60)
    print("\nOpening browser for Fyers login...")
    
    session = fyersModel.SessionModel(
        client_id=CLIENT_ID,
        secret_key=SECRET_KEY,
        redirect_uri=REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code"
    )
    
    auth_url = session.generate_authcode()
    
    # Open in user's default browser
    webbrowser.open(auth_url, new=1)
    
    # Step 2: Ask user for auth_code
    print("\n" + "-" * 60)
    print("⚠️  ACTION REQUIRED:")
    print("-" * 60)
    print("1. Browser should now be open to Fyers login page")
    print("2. Log in with your Fyers account")
    print("3. After login, look at the URL in your browser")
    print("4. Copy the entire URL or find the 'auth_code' parameter")
    print()
    auth_code = input("📋 Paste the 'auth_code' from the URL here: ").strip()
    
    if not auth_code:
        print("❌ No auth_code provided. Authentication cancelled.")
        return None
    
    # Step 3: Exchange auth_code for access_token
    print("\n🔄 Exchanging auth_code for access_token...")
    session.set_token(auth_code)
    response = session.generate_token()
    
    if "access_token" not in response:
        print("❌ Login Failed!")
        print("   Response:", response)
        print("\nPossible reasons:")
        print("  • auth_code expired (be quick next time)")
        print("  • Invalid client_id/secret_key in config/keys.py")
        print("  • Fyers API temporarily unavailable")
        return None
    
    access_token = response["access_token"]
    refresh_token = response.get("refresh_token", "")
    
    # Step 4: Save token locally
    print("💾 Saving token to local file...")
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    
    with open(token_path, "w") as f:
        f.write(access_token)
    
    # Set file modification time to today
    import time
    os.utime(token_path, None)  # This sets atime/mtime to now
    
    print(f"✅ Token saved successfully to: {token_path}")
    print(f"⏰ Token valid until: ~6:30 AM IST tomorrow")
    print(f"   (Fyers tokens expire daily around 6:30 AM IST)")
    
    # Also save refresh_token for future use if needed
    if refresh_token:
        refresh_path = token_path + ".refresh"
        with open(refresh_path, "w") as f:
            f.write(refresh_token)
        print(f"🔄 Refresh token also saved to: {refresh_path}")
    
    return access_token


def quick_validate_token() -> bool:
    """Quick validation: does a valid token exist?"""
    return read_valid_token() is not None


def read_valid_token() -> Optional[str]:
    """NON-INTERACTIVE token read (used by engine.py / app.py).

    Returns today's access token from disk, or None if missing/expired.
    Never prompts — per Option A, login only happens via:
        python core/auth.py
    """
    token_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "data", "fyers_token.txt"
    )
    token_path = os.path.normpath(token_path)

    if not os.path.exists(token_path):
        return None

    file_date = date.fromtimestamp(os.path.getmtime(token_path))
    if file_date != date.today():
        return None  # Fyers tokens expire daily (~6:30 AM IST)

    with open(token_path, "r") as f:
        token = f.read().strip()

    if not token or token == "YOUR_FYERS_ACCESS_TOKEN":
        return None
    return token


def get_token() -> Optional[str]:
    """Get the access token; falls back to interactive login if needed.

    Prefer read_valid_token() in non-interactive contexts (engine/app).
    """
    token = read_valid_token()
    if token:
        return token

    print("🔐 No valid token found. Running interactive authentication...")
    return authenticate_interactive()


if __name__ == "__main__":
    # Standalone execution: user runs this in their terminal
    print("\n" + "=" * 60)
    print("FYERS OPTION A AUTHENTICATION")
    print("=" * 60 + "\n")
    
    token = authenticate_interactive()
    
    if token:
        print("\n" + "=" * 60)
        print("✅ Authentication Complete!")
        print("=" * 60)
        print("\nYou can now run the screener:")
        print("   python engine.py")
        print("   OR")
        print("   python test_hilega_milega.py")
    else:
        print("\n" + "=" * 60)
        print("❌ Authentication Failed")
        print("=" * 60)
        sys.exit(1)