#!/usr/bin/env python3
"""
Fetches Smartlead + Aimfox data and writes data/cache.json.
Run by GitHub Actions on a schedule; the cached file is committed to the
data-cache branch so the dashboard can serve it without live API calls.
"""
import os, sys, json, time, requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

SL_KEY  = os.getenv("SMARTLEAD_API_KEY", "")
AF_KEY  = os.getenv("AIMFOX_API_KEY", "")
SL_BASE = "https://server.smartlead.ai/api/v1"
AF_BASE = "https://api.aimfox.com/api/v1"
CLIENT_FILTER = os.getenv("CLIENT_FILTER", "FEAAM")
AF_ACCOUNTS = {
    "439119116":  "Ebru AVCI",
    "1723192451": "Dr. Gerling",
}

if not SL_KEY:
    print(f"{datetime.now()} - SMARTLEAD_API_KEY not set")
    sys.exit(1)
if not AF_KEY:
    print(f"{datetime.now()} - AIMFOX_API_KEY not set")
    sys.exit(1)


def sl(path, params=None):
    p = dict(params or {}); p["api_key"] = SL_KEY
    for attempt in range(4):
        r = requests.get(f"{SL_BASE}{path}", params=p, timeout=30)
        if r.status_code == 429:
            wait = 15 * (2 ** attempt)
            print(f"  SL rate-limited, waiting {wait}s…")
            time.sleep(wait)
            continue
        r.raise_for_status(); return r.json()
    r.raise_for_status(); return r.json()


def af(path, params=None):
    r = requests.get(f"{AF_BASE}{path}", params=params or {},
                     headers={"Authorization": f"Bearer {AF_KEY}"}, timeout=30)
    r.raise_for_status(); return r.json()


_ws_id = None
def af_ws(path, params=None):
    global _ws_id
    if not _ws_id:
        _ws_id = af("/me")["token"]["workspace_id"]
    return af(f"/workspaces/{_ws_id}{path}", params)


def safe_int(v): return int(v or 0)
def pct(a, b): return round(a / b * 100, 1) if b else 0


def fetch_smartlead():
    end = datetime.today(); start = end - timedelta(days=29)
    sd, ed = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    all_camps = sl("/campaigns")
    if isinstance(all_camps, dict):
        all_camps = all_camps.get("data", [])

    feaam_main = {c["id"]: c for c in all_camps
                  if CLIENT_FILTER.lower() in c.get("name", "").lower()
                  and not c.get("parent_campaign_id")}

    seen = set()
    feaam_subs = []
    for c in all_camps:
        if c.get("parent_campaign_id") in feaam_main and c["id"] not in seen:
            feaam_subs.append(c); seen.add(c["id"])

    def fetch_campaign(c, is_sub=False):
        cid = c["id"]; cname = c.get("name", f"Campaign {cid}")
        try:
            d = sl(f"/campaigns/{cid}/analytics-by-date",
                   {"start_date": sd, "end_date": ed})
        except Exception: d = {}
        try:
            full = sl(f"/campaigns/{cid}/analytics")
        except Exception: full = {}

        ls = full.get("campaign_lead_stats", {})
        total     = safe_int(ls.get("total"))
        completed = safe_int(ls.get("completed"))
        interested= safe_int(ls.get("interested"))
        u_sent    = safe_int(d.get("unique_sent_count"))
        u_open    = safe_int(d.get("unique_open_count"))
        replied   = safe_int(d.get("reply_count"))

        return {
            "id":           cid,
            "name":         cname,
            "status":       full.get("status", c.get("status", "UNKNOWN")),
            "created_at":   c.get("created_at", ""),
            "sequence_count": safe_int(full.get("sequence_count") or c.get("sequence_count")),
            "parent_id":    c.get("parent_campaign_id"),
            "client_name":  full.get("client_name") or "",
            "is_sub":       is_sub,
            "progress":     pct(completed, total),
            "total_leads":  total,
            "sent":         safe_int(d.get("sent_count")),
            "unique_sent":  u_sent,
            "opened":       safe_int(d.get("open_count")),
            "unique_opened":u_open,
            "replied":      replied,
            "positive":     interested,
            "bounced":      safe_int(d.get("bounce_count")),
            "clicked":      safe_int(d.get("click_count")),
            "open_pct":     pct(u_open, u_sent),
            "reply_pct":    pct(replied, u_sent),
            "positive_pct": pct(interested, replied) if replied else 0,
        }

    campaigns_out = {}; subs_out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        mf = {ex.submit(fetch_campaign, c, False): c["id"] for c in feaam_main.values()}
        sf = {ex.submit(fetch_campaign, c, True): c["id"] for c in feaam_subs}
        for fut in mf:
            try: campaigns_out[mf[fut]] = fut.result()
            except Exception as e: print(f"  SL campaign error: {e}")
        for fut in sf:
            try: subs_out.append(fut.result())
            except Exception as e: print(f"  SL sub error: {e}")

    for sub in subs_out:
        pid = sub["parent_id"]
        if pid in campaigns_out:
            sl_list = campaigns_out[pid].setdefault("subsequences", [])
            if not any(s["id"] == sub["id"] for s in sl_list):
                sl_list.append(sub)

    result = sorted(campaigns_out.values(), key=lambda x: x.get("created_at", ""), reverse=True)
    totals = {k: sum(c.get(k, 0) for c in result)
              for k in ["sent", "opened", "replied", "positive", "bounced"]}
    return {"ok": True, "campaigns": result, "totals": totals,
            "fetched_at": datetime.now().isoformat()}


def fetch_aimfox():
    target_owner_ids = set(AF_ACCOUNTS.keys())
    all_camps = af_ws("/campaigns").get("campaigns", [])
    feaam_camps = [c for c in all_camps
                   if any(str(o) in target_owner_ids for o in c.get("owners", []))]

    def fetch_af_campaign(c):
        cid = c["id"]; cname = c.get("name", f"Campaign {cid}")
        owners = [str(o) for o in c.get("owners", [])]
        owner_names = [AF_ACCOUNTS[o] for o in owners if o in AF_ACCOUNTS]
        created_ms = c.get("created_at", 0)
        created_at = (datetime.fromtimestamp(created_ms / 1000).isoformat()
                      if created_ms else "")
        try:
            m = af_ws(f"/campaigns/{cid}/metrics").get("metrics", {})
            sent     = safe_int(m.get("sent_connections"))
            accepted = safe_int(m.get("accepted_connections"))
            messages = safe_int(m.get("sent_messages"))
            replied  = safe_int(m.get("replies"))
        except Exception:
            sent = accepted = messages = replied = 0

        owner_stats = {oid: {"sent": 0, "accepted": 0} for oid in target_owner_ids}
        try:
            audience = af_ws(f"/campaigns/{cid}/audience", {"limit": 500}).get("audience", [])
            for a in audience:
                oid = str(a.get("owner", ""))
                s   = a.get("state", "")
                if oid in target_owner_ids:
                    owner_stats[oid]["sent"] += 1
                    if s in ("message", "reply", "done", "connect"):
                        owner_stats[oid]["accepted"] += 1
        except Exception:
            pass

        return {
            "id":           cid,
            "name":         cname,
            "state":        c.get("state", ""),
            "created_at":   created_at,
            "owners":       owners,
            "owner_names":  owner_names,
            "target_count": safe_int(c.get("target_count")),
            "sent":         sent,
            "accepted":     accepted,
            "messages":     messages,
            "replied":      replied,
            "accept_pct":   pct(accepted, sent),
            "reply_pct":    pct(replied, sent),
            "owner_stats":  owner_stats,
        }

    result = []
    with ThreadPoolExecutor(max_workers=5) as ex:
        for r in ex.map(fetch_af_campaign, feaam_camps):
            result.append(r)
    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    account_totals = {oid: {"sent": 0, "accepted": 0, "messages": 0, "replied": 0}
                      for oid in target_owner_ids}
    for c in result:
        for oid in c["owners"]:
            if oid in account_totals:
                account_totals[oid]["sent"]     += c["sent"]
                account_totals[oid]["accepted"] += c["accepted"]
                account_totals[oid]["messages"] += c["messages"]
                account_totals[oid]["replied"]  += c["replied"]

    accounts_out = [{"id": oid, "name": name, "totals": account_totals.get(oid, {})}
                    for oid, name in AF_ACCOUNTS.items()]
    return {"ok": True, "campaigns": result, "accounts": accounts_out,
            "fetched_at": datetime.now().isoformat()}


def fetch_all_positives(sl_campaigns, af_campaigns):
    """Fetch positive/replied lead lists for all campaigns (no message threads)."""
    sl_leads, af_leads = [], []

    def fetch_sl_positives(c):
        cid = c["id"]; cname = c.get("name", f"Campaign {cid}")
        found = []
        offset, limit = 0, 100
        while True:
            try:
                p = {"offset": offset, "limit": limit, "api_key": SL_KEY}
                r = requests.get(f"{SL_BASE}/campaigns/{cid}/leads", params=p, timeout=20)
                if not r.ok: break
                raw  = r.json()
                page = raw if isinstance(raw, list) \
                       else raw.get("list", raw.get("data", raw.get("leads", [])))
                for lead in page:
                    cat = (lead.get("lead_category") or lead.get("category") or
                           lead.get("status") or lead.get("campaign_status") or "").lower()
                    if "interest" not in cat:
                        continue
                    first = lead.get("first_name", "")
                    last  = lead.get("last_name",  "")
                    email = lead.get("email", "")
                    found.append({
                        "name": f"{first} {last}".strip() or email,
                        "email": email,
                        "campaign": cname,
                        "campaign_id": cid,
                    })
                if len(page) < limit: break
                offset += limit
            except Exception:
                break
        return found

    def fetch_af_replied(c):
        cid = c["id"]; cname = c.get("name", f"Campaign {cid}")
        found = []
        try:
            raw      = af_ws(f"/campaigns/{cid}/audience", {"limit": 500})
            audience = raw.get("audience", raw if isinstance(raw, list) else [])
            for a in audience:
                if (a.get("state") or "").lower() != "reply": continue
                profile = a.get("profile") or a
                first   = profile.get("first_name", a.get("first_name", ""))
                last    = profile.get("last_name",  a.get("last_name",  ""))
                name    = f"{first} {last}".strip() or profile.get("name", "")
                lid     = a.get("id") or a.get("profile_id")
                found.append({
                    "name": name or f"Lead {lid}",
                    "id": lid,
                    "campaign": cname,
                    "campaign_id": cid,
                })
        except Exception:
            pass
        return found

    with ThreadPoolExecutor(max_workers=8) as ex:
        for batch in ex.map(fetch_sl_positives, sl_campaigns):
            sl_leads.extend(batch)

    af_target_ids = set(AF_ACCOUNTS.keys())
    af_feaam = [c for c in af_campaigns
                if any(str(o) in af_target_ids for o in c.get("owners", []))]
    with ThreadPoolExecutor(max_workers=5) as ex:
        for batch in ex.map(fetch_af_replied, af_feaam):
            af_leads.extend(batch)

    return {"ok": True, "smartlead": sl_leads, "aimfox": af_leads,
            "fetched_at": datetime.now().isoformat()}


if __name__ == "__main__":
    print(f"{datetime.now()} - Fetching Smartlead...")
    sl_data = fetch_smartlead()
    print(f"  {len(sl_data['campaigns'])} campaigns, totals: {sl_data['totals']}")

    print(f"{datetime.now()} - Fetching Aimfox...")
    af_data = fetch_aimfox()
    print(f"  {len(af_data['campaigns'])} campaigns")

    print(f"{datetime.now()} - Fetching positive/replied leads...")
    # Collect all FEAAM campaigns (main + subs) for positives sweep
    all_sl = []
    for c in sl_data["campaigns"]:
        all_sl.append(c)
        all_sl.extend(c.get("subsequences", []))
    pos_data = fetch_all_positives(all_sl, af_data["campaigns"])
    sl_pos = len(pos_data["smartlead"]); af_pos = len(pos_data["aimfox"])
    print(f"  {sl_pos} SL positive, {af_pos} AF replied")

    cache = {
        "smartlead":    sl_data,
        "aimfox":       af_data,
        "all_positives": pos_data,
        "cached_at":    datetime.now().isoformat(),
    }
    os.makedirs("data", exist_ok=True)
    with open("data/cache.json", "w") as f:
        json.dump(cache, f, indent=2)

    print(f"{datetime.now()} - Wrote data/cache.json")
