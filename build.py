#!/usr/bin/env python3
"""Fetch latest FunnelFox data (and optionally Stripe) and swap it into index.html.
Reads FOX_SECRET (required) and STRIPE_SECRET (optional) from env. Keeps all UI code
intact, only replaces `const RAW = [...]`, `const STRIPE_RAW = [...]` and the BUILT_AT
stamps. If STRIPE_SECRET is missing, the Stripe block is left untouched."""
import os, re, json, sys, datetime
import urllib.request, urllib.error, urllib.parse
from concurrent.futures import ThreadPoolExecutor

SECRET = os.environ.get("FOX_SECRET")
if not SECRET:
    sys.exit("ERROR: FOX_SECRET env var not set")
STRIPE_SECRET = os.environ.get("STRIPE_SECRET")
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

# ---------------- Stripe ----------------
SBASE = "https://api.stripe.com/v1"

def stripe_api(path, params=None):
    url = SBASE + path
    if params:
        # Stripe expects repeated keys for arrays (expand[]); urlencode with doseq
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + STRIPE_SECRET})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError):
            if attempt == 3:
                raise
    return None

def stripe_list(path, base_params):
    """Auto-paginate a Stripe list endpoint."""
    out, starting_after = [], None
    while True:
        params = dict(base_params); params["limit"] = "100"
        if starting_after:
            params["starting_after"] = starting_after
        d = stripe_api(path, params)
        out += d.get("data", [])
        if not d.get("has_more"):
            break
        starting_after = d["data"][-1]["id"]
    return out

def iso(ts):
    if not ts:
        return None
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def fetch_stripe():
    """Return list of subscription records with REAL renewal counts, or None to skip."""
    if not STRIPE_SECRET:
        print("STRIPE_SECRET not set — skipping Stripe (STRIPE_RAW left untouched).")
        return None
    print("Fetching Stripe subscriptions...")
    subs = stripe_list("/subscriptions", {"status": "all", "expand[]": "data.items.data.price"})
    print(f"  {len(subs)} Stripe subscriptions")
    print("Counting real renewals from paid subscription_cycle invoices...")
    cycle = {}
    for inv in stripe_list("/invoices", {}):
        if inv.get("billing_reason") == "subscription_cycle" and inv.get("status") == "paid":
            sid = inv.get("subscription")
            if sid:
                cycle[sid] = cycle.get(sid, 0) + 1
    out = []
    for s in subs:
        items = ((s.get("items") or {}).get("data")) or []
        price = (items[0].get("price") if items else None) or {}
        rec = (price.get("recurring") or {})
        out.append({
            "created_at": iso(s.get("created")),
            "status": s.get("status"),
            "billing_interval": rec.get("interval"),
            "billing_interval_count": rec.get("interval_count"),
            "price": price.get("unit_amount"),
            "currency": price.get("currency"),
            "renewals": cycle.get(s.get("id"), 0),
            "canceled_at": iso(s.get("canceled_at")),
            "updated_at": iso(s.get("canceled_at") or s.get("current_period_start") or s.get("created")),
            "product": price.get("nickname"),
        })
    return out

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

    # Stripe (optional)
    try:
        stripe_out = fetch_stripe()
    except Exception as e:
        print(f"WARNING: Stripe fetch failed ({e}); leaving STRIPE_RAW untouched.")
        stripe_out = None
    if stripe_out is not None:
        sdata = json.dumps(stripe_out, separators=(",", ":"))
        new = re.sub(r'const STRIPE_RAW = .*?;\n', 'const STRIPE_RAW = ' + sdata + ';\n', new, count=1, flags=re.S)
        new = re.sub(r'const STRIPE_BUILT_AT\s*=\s*"[^"]*";', f'const STRIPE_BUILT_AT = "{stamp}";', new, count=1)
        print(f"  Stripe: {len(stripe_out)} subscription records embedded.")

    open("index.html", "w", encoding="utf-8").write(new)
    open("renew-cohort-report.html", "w", encoding="utf-8").write(new)
    print(f"Done: {len(out)} FunnelFox records, built at {stamp}")

if __name__ == "__main__":
    main()
