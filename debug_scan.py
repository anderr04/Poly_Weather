#!/usr/bin/env python3
"""Quick diagnostic: check Madrid slug + token prices."""
import sys, json, requests
sys.path.insert(0, ".")

s = requests.Session()
s.headers.update({"User-Agent": "Poly_Weather/1.0", "Accept": "application/json"})
GAMMA = "https://gamma-api.polymarket.com"

# Test Madrid and other cities
slugs_to_test = [
    "highest-temperature-in-madrid-on-march-23",
    "highest-temperature-in-madrid-on-march-24",
    "highest-temperature-in-berlin-on-march-23",
    "highest-temperature-in-paris-on-march-23",
    "highest-temperature-in-munich-on-march-23",
    "highest-temperature-in-sydney-on-march-23",
    "highest-temperature-in-singapore-on-march-23",
    "highest-temperature-in-new-york-on-march-23",
    "highest-temperature-in-tokyo-on-march-23",
    "highest-temperature-in-vienna-on-march-23",
    "highest-temperature-in-rome-on-march-23",
    "highest-temperature-in-amsterdam-on-march-23",
]

for slug in slugs_to_test:
    r = s.get(f"{GAMMA}/events", params={"slug": slug}, timeout=10)
    data = r.json() if r.status_code == 200 else []
    if data:
        ev = data[0] if isinstance(data, list) else data
        title = ev.get("title", "")
        markets = ev.get("markets", [])
        print(f"[FOUND] {slug}")
        print(f"  title: {title}")
        print(f"  markets: {len(markets)}")
        if markets:
            m = markets[0]
            print(f"  first question: {m.get('question', '')[:80]}")
            print(f"  outcomePrices: {m.get('outcomePrices', '')}")
            print(f"  outcomes: {m.get('outcomes', '')}")
            print(f"  tokens: {m.get('tokens', [])[:2]}")
            print(f"  clobTokenIds: {str(m.get('clobTokenIds', ''))[:80]}")
    else:
        print(f"[MISS]  {slug}")
    import time; time.sleep(0.15)
