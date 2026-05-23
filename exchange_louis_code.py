#!/usr/bin/env python3
"""
Run this once after Louis sends you his code, client ID and client secret:
    python3 exchange_louis_code.py <CODE> <CLIENT_ID> <CLIENT_SECRET>
"""
import json, sys
from pathlib import Path
import requests

TOKEN_DIR  = Path.home() / ".strava-louis"
TOKEN_FILE = TOKEN_DIR / "tokens.json"

if len(sys.argv) < 4:
    print("Usage: python3 exchange_louis_code.py <CODE> <CLIENT_ID> <CLIENT_SECRET>")
    sys.exit(1)

code, client_id, client_secret = sys.argv[1].strip(), sys.argv[2].strip(), sys.argv[3].strip()
print(f"Exchanging code for Louis (client_id={client_id})…")

r = requests.post("https://www.strava.com/oauth/token", data={
    "client_id":     client_id,
    "client_secret": client_secret,
    "code":          code,
    "grant_type":    "authorization_code",
})
r.raise_for_status()
data = r.json()

TOKEN_DIR.mkdir(exist_ok=True)
tokens = {
    "client_id":     client_id,
    "client_secret": client_secret,
    "access_token":  data["access_token"],
    "refresh_token": data["refresh_token"],
    "expires_at":    data["expires_at"],
}
TOKEN_FILE.write_text(json.dumps(tokens, indent=2))
name = data.get("athlete", {}).get("firstname", "Louis")
print(f"✓ Tokens saved for {name}!")
print(f"  Run: python3 generate_dashboard.py")
