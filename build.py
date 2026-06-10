#!/usr/bin/env python3
"""Fetch latest FunnelFox data and swap it into index.html.
Reads FOX_SECRET from env. Keeps all UI code intact, only replaces `const RAW = [...]`
and the BUILT_AT stamp."""
import os, re, json, sys, datetime
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

SECRET = os.environ.get("FOX_SECRET")
if not SECRET:
    sys.exit("ERROR: FOX_SECRET env var not set")
BASE = "https://api.funnelfox.io/public/v1"
KEYS = ["created_at","period_starts_at","billing_interval","billing_interval_count",
        "status","price","price_usd","funnel_version","renews","currency","updated_at"]

def api(path):
    req = urllib.request.Request(BASE + path, headers={"Fox-Secret": SECRET})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 3:
                raise
    return None

def fetch_subscriptions():
    subs, cursor = [], None
    while True:
        p = "/subscriptions?limit=500" + (f"&cursor={cursor}" if cursor else "")
        d = api(p)
        subs += d["data"]
        if not d["pagination"]["has_more"]:
            break
        cursor = d["pagination"]["next_cursor"]
    return subs

def funnel_of(sub):
    try:
        d = api(f"/subscriptions/{sub['id']}")
        return (d.get("funnel") or {}).get("title") or "(none)"
    except Exception:
        return "(none)"

def main():
    print("Fetching subscriptions...")
    subs = fetch_subscriptions()
    print(f"  {len(subs)} subscriptions")
    print("Enriching funnel names (detail endpoint)...")
    with ThreadPoolExecutor(max_workers=24) as ex:
        funnels = list(ex.map(funnel_of, subs))
    out = []
    for s, f in zip(subs, funnels):
        r = {k: s.get(k) for k in KEYS}
        r["funnel"] = f
        out.append(r)
    data = json.dumps(out, separators=(",", ":"))
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = open("index.html", encoding="utf-8").read()
    new = re.sub(r'const RAW = .*?;\n', 'const RAW = ' + data + ';\n', html, count=1, flags=re.S)
    new = re.sub(r'const BUILT_AT\s*=\s*"[^"]*";', f'const BUILT_AT = "{stamp}";', new, count=1)
    if new == html:
        print("WARNING: nothing replaced (RAW pattern not found?)")
    open("index.html", "w", encoding="utf-8").write(new)
    open("renew-cohort-report.html", "w", encoding="utf-8").write(new)
    print(f"Done: {len(out)} records, built at {stamp}")

if __name__ == "__main__":
    main()
