"""
Quick connection test for Angel One SmartAPI.
Run this BEFORE starting the server to confirm credentials work.

Usage:
    .venv\Scripts\python.exe test_angel.py
"""
import os, sys
from dotenv import load_dotenv
load_dotenv()

from app.data_sources.angel import (
    test_connection, get_option_chain, get_ltp,
    ANGEL_AVAILABLE, ANGEL_CLIENT_ID, ANGEL_API_KEY,
)

print("=" * 55)
print("Angel One SmartAPI — Connection Test")
print("=" * 55)
print(f"API Key    : {ANGEL_API_KEY[:4]}{'*' * (len(ANGEL_API_KEY)-4) if len(ANGEL_API_KEY) > 4 else ''}")
print(f"Client ID  : {ANGEL_CLIENT_ID}")
print(f"Configured : {ANGEL_AVAILABLE}")
print()

# Step 1: Login
print("[ 1 ] Testing login...")
result = test_connection()
print(f"      Status  : {result['status']}")
print(f"      Message : {result['message']}")
if result["status"] != "ok":
    print("\nFix the error above then re-run.")
    sys.exit(1)

print()

# Step 2: Option chain for NIFTY
print("[ 2 ] Fetching NIFTY option chain...")
oc = get_option_chain("NIFTY")
if oc:
    print(f"      Expiry    : {oc['expiry']}")
    print(f"      Spot      : {oc['spotPrice']}")
    print(f"      ATM       : {oc['atm']}")
    print(f"      PCR       : {oc['pcr']}")
    print(f"      Max Pain  : {oc['maxPain']}")
    print(f"      CE rows   : {len(oc['calls'])}")
    print(f"      PE rows   : {len(oc['puts'])}")
    # Show top 3 strikes by CE OI
    top_ce = sorted(oc["calls"], key=lambda x: x["oi"], reverse=True)[:3]
    print("      Top CE OI strikes:")
    for row in top_ce:
        print(f"        Strike {row['strike']} | OI {row['oi']:,} | LTP {row['ltp']} | IV {row['iv']}%")
else:
    print("      FAILED — option chain returned None")

print()

# Step 3: BANKNIFTY option chain
print("[ 3 ] Fetching BANKNIFTY option chain...")
oc2 = get_option_chain("BANKNIFTY")
if oc2:
    print(f"      Spot {oc2['spotPrice']} | ATM {oc2['atm']} | PCR {oc2['pcr']} | Expiry {oc2['expiry']}")
else:
    print("      FAILED")

print()
print("=" * 55)
if oc and oc2:
    print("ALL TESTS PASSED — Angel One integration is working.")
    print("Add credentials to .env and start the server.")
else:
    print("PARTIAL — login works but option chain fetch failed.")
    print("Check if the TOTP code is correct and not expired.")
print("=" * 55)
