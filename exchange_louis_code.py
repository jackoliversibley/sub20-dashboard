#!/usr/bin/env python3
"""
Run this once after Louis authorises on Strava:
    python3 exchange_louis_code.py <CODE>

The code comes from the callback page he was redirected to.
"""
import json, sys
from pathlib import Path
import requests

CLIENT_ID     = "248666"
CLIENT_SECRET = "52a741de3d3257c5daaa2e9449fe2a5f35573c02"
TOKEN_DIR     = Path.home() / ".strava-louis"
TOKEN_FILE    = TOKEN_DIR / "tokens.json"

if len(sys.argv) < 2:
    print("Usage: python3 exchange_louis_code.py <CODE>")
    sys.exit(1)

code = sys.argv[1].strip()
print(f"Exchanging code {code[:8]}…")

r = requests.post("https://www.strava.com/oauth/token", data={
    "client_id":     CLIENT_ID,
    "client_secret": CLIENT_SECRET,
    "code":          code,
    "grant_type":    "authorization_code",
})
r.raise_for_status()
data = r.json()

TOKEN_DIR.mkdir(exist_ok=True)
tokens = {
    "access_token":  data["access_token"],
    "refresh_token": data["refresh_token"],
    "expires_at":    data["expires_at"],
}
TOKEN_FILE.write_text(json.dumps(tokens, indent=2))
name = data.get("athlete", {}).get("firstname", "Louis")
print(f"✓ Tokens saved for {name}. Louis is now connected to the dashboard!")
print(f"  Stored at: {TOKEN_FILE}")
print("  Run 'python3 generate_dashboard.py' to regenerate with his data.")
