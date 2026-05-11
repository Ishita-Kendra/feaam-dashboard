from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import requests
import os, json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

CACHE_FILE = os.path.join(os.path.dirname(__file__), "data", "cache.json")

def _load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return None

load_dotenv()

app = Flask(__name__)
CORS(app)

SL_KEY   = os.getenv("SMARTLEAD_API_KEY", "")
SL_BASE  = "https://server.smartlead.ai/api/v1"

AF_KEY   = os.getenv("AIMFOX_API_KEY", "")
AF_BASE  = "https://api.aimfox.com/api/v1"

CLIENT_FILTER = os.getenv("CLIENT_FILTER", "FEAAM")

# Aimfox account IDs (hardcoded, stable)
AF_ACCOUNTS = {
    "439119116":  "Ebru AVCI",
    "1723192451": "Dr. Gerling",
}

_af_workspace_id = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def sl(path, params=None):
    p = dict(params or {}); p["api_key"] = SL_KEY
    r = requests.get(f"{SL_BASE}{path}", params=p, timeout=20)
    r.raise_for_status(); return r.json()


def af(path, params=None):
    r = requests.get(f"{AF_BASE}{path}", params=params or {},
                     headers={"Authorization": f"Bearer {AF_KEY}"}, timeout=20)
    r.raise_for_status(); return r.json()


def af_ws(path, params=None):
    ws = get_af_workspace()
    return af(f"/workspaces/{ws}{path}", params)


def get_af_workspace():
    global _af_workspace_id
    if not _af_workspace_id:
        _af_workspace_id = af("/me")["token"]["workspace_id"]
    return _af_workspace_id


def default_dates():
    e = datetime.today(); s = e - timedelta(days=29)
    return s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")


def safe_int(v): return int(v or 0)
def pct(a, b): return round(a / b * 100, 1) if b else 0


# ── Smartlead ─────────────────────────────────────────────────────────────────

@app.route("/api/smartlead")
def smartlead():
    start = request.args.get("start_date")
    end   = request.args.get("end_date")
    if not start or not end:
        start, end = default_dates()
        cache = _load_cache()
        if cache and cache.get("smartlead", {}).get("ok"):
            return jsonify(cache["smartlead"])

    try:
        all_camps = sl("/campaigns")
        if isinstance(all_camps, dict):
            all_camps = all_camps.get("data", [])

        # Main FEAAM campaigns (no parent)
        feaam_main = {c["id"]: c for c in all_camps
                      if CLIENT_FILTER.lower() in c.get("name", "").lower()
                      and not c.get("parent_campaign_id")}

        # Subsequences: child of any FEAAM main campaign (deduplicated by id)
        seen_sub_ids = set()
        feaam_subs = []
        for c in all_camps:
            if c.get("parent_campaign_id") in feaam_main and c["id"] not in seen_sub_ids:
                feaam_subs.append(c)
                seen_sub_ids.add(c["id"])

        def fetch_campaign(c, is_sub=False):
            cid   = c["id"]
            cname = c.get("name", f"Campaign {cid}")

            # Date-range analytics
            try:
                d = sl(f"/campaigns/{cid}/analytics-by-date",
                       {"start_date": start, "end_date": end})
            except Exception:
                d = {}

            sent         = safe_int(d.get("sent_count"))
            opened       = safe_int(d.get("open_count"))
            unique_sent  = safe_int(d.get("unique_sent_count"))
            unique_open  = safe_int(d.get("unique_open_count"))
            replied      = safe_int(d.get("reply_count"))
            bounced      = safe_int(d.get("bounce_count"))
            clicked      = safe_int(d.get("click_count"))

            # All-time details (status, sequences, lead stats, interested)
            try:
                full = sl(f"/campaigns/{cid}/analytics")
            except Exception:
                full = {}

            lead_stats = full.get("campaign_lead_stats", {})
            total_leads    = safe_int(lead_stats.get("total"))
            completed_leads= safe_int(lead_stats.get("completed"))
            interested     = safe_int(lead_stats.get("interested"))  # positive replies
            progress       = pct(completed_leads, total_leads)
            status         = full.get("status", c.get("status", "UNKNOWN"))
            seq_count      = safe_int(full.get("sequence_count") or c.get("sequence_count"))
            client_name    = full.get("client_name") or ""
            created_at     = c.get("created_at", "")
            parent_id      = c.get("parent_campaign_id")

            return {
                "id":           cid,
                "name":         cname,
                "status":       status,
                "created_at":   created_at,
                "sequence_count": seq_count,
                "parent_id":    parent_id,
                "client_name":  client_name,
                "is_sub":       is_sub,
                "progress":     progress,
                "total_leads":  total_leads,
                "sent":         sent,
                "unique_sent":  unique_sent,
                "opened":       opened,
                "unique_opened":unique_open,
                "replied":      replied,
                "positive":     interested,
                "bounced":      bounced,
                "clicked":      clicked,
                "open_pct":     pct(unique_open, unique_sent),
                "reply_pct":    pct(replied, unique_sent),
                "positive_pct": pct(interested, replied) if replied else 0,
            }

        # Fetch all in parallel
        campaigns_out = {}
        subs_out = []

        with ThreadPoolExecutor(max_workers=8) as ex:
            main_futs = {ex.submit(fetch_campaign, c, False): c["id"]
                         for c in feaam_main.values()}
            sub_futs  = {ex.submit(fetch_campaign, c, True): c["id"]
                         for c in feaam_subs}

            for fut, cid in main_futs.items():
                try: campaigns_out[cid] = fut.result()
                except Exception: pass

            for fut in sub_futs:
                try: subs_out.append(fut.result())
                except Exception: pass

        # Attach subs to parents (deduplicate by sub id)
        for sub in subs_out:
            pid = sub["parent_id"]
            if pid in campaigns_out:
                subs_list = campaigns_out[pid].setdefault("subsequences", [])
                if not any(s["id"] == sub["id"] for s in subs_list):
                    subs_list.append(sub)

        # Sort by created_at newest first
        result = sorted(campaigns_out.values(),
                        key=lambda x: x.get("created_at", ""),
                        reverse=True)

        # Totals
        totals = {k: sum(c.get(k, 0) for c in result)
                  for k in ["sent", "opened", "replied", "positive", "bounced"]}

        return jsonify({"ok": True, "campaigns": result, "totals": totals,
                        "fetched_at": datetime.now().isoformat()})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Aimfox ────────────────────────────────────────────────────────────────────

@app.route("/api/aimfox")
def aimfox():
    cache = _load_cache()
    if cache and cache.get("aimfox", {}).get("ok"):
        return jsonify(cache["aimfox"])
    try:
        ws = get_af_workspace()
        all_camps = af_ws("/campaigns").get("campaigns", [])

        # Filter: only campaigns owned by Ebru or Gerling
        target_owner_ids = set(AF_ACCOUNTS.keys())
        feaam_camps = []
        for c in all_camps:
            owners = [str(o) for o in c.get("owners", [])]
            if any(o in target_owner_ids for o in owners):
                feaam_camps.append(c)

        def fetch_af_campaign(c):
            cid    = c["id"]
            cname  = c.get("name", f"Campaign {cid}")
            owners = [str(o) for o in c.get("owners", [])]
            owner_names = [AF_ACCOUNTS[o] for o in owners if o in AF_ACCOUNTS]
            state  = c.get("state", "")
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

            # Get audience to find accepted leads with counts per owner
            owner_stats = {oid: {"sent": 0, "accepted": 0} for oid in target_owner_ids}
            try:
                audience = af_ws(f"/campaigns/{cid}/audience",
                                 {"limit": 500}).get("audience", [])
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
                "id":          cid,
                "name":        cname,
                "state":       state,
                "created_at":  created_at,
                "owners":      owners,
                "owner_names": owner_names,
                "target_count": safe_int(c.get("target_count")),
                "sent":        sent,
                "accepted":    accepted,
                "messages":    messages,
                "replied":     replied,
                "accept_pct":  pct(accepted, sent),
                "reply_pct":   pct(replied, sent),
                "owner_stats": owner_stats,
            }

        result = []
        with ThreadPoolExecutor(max_workers=5) as ex:
            for r in ex.map(fetch_af_campaign, feaam_camps):
                result.append(r)

        result.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        # Per-account totals
        account_totals = {oid: {"sent": 0, "accepted": 0, "messages": 0, "replied": 0}
                          for oid in target_owner_ids}
        for c in result:
            for oid in c["owners"]:
                if oid in account_totals:
                    account_totals[oid]["sent"]     += c["sent"]
                    account_totals[oid]["accepted"] += c["accepted"]
                    account_totals[oid]["messages"] += c["messages"]
                    account_totals[oid]["replied"]  += c["replied"]

        accounts_out = [{"id": oid, "name": name,
                         "totals": account_totals.get(oid, {})}
                        for oid, name in AF_ACCOUNTS.items()]

        return jsonify({"ok": True, "campaigns": result, "accounts": accounts_out,
                        "fetched_at": datetime.now().isoformat()})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Positive Leads ───────────────────────────────────────────────────────────

@app.route("/api/smartlead/positive-leads/<int:campaign_id>")
def positive_leads(campaign_id):
    try:
        all_leads = []
        offset, limit = 0, 100
        while True:
            p = {"offset": offset, "limit": limit, "api_key": SL_KEY}
            r = requests.get(f"{SL_BASE}/campaigns/{campaign_id}/leads",
                             params=p, timeout=20)
            if not r.ok:
                return jsonify({"ok": False,
                                "error": f"Smartlead {r.status_code}: {r.text[:300]}"}), 500
            raw = r.json()

            if isinstance(raw, list):
                page = raw
            elif isinstance(raw, dict):
                page = raw.get("list", raw.get("data", raw.get("leads", [])))
            else:
                page = []

            all_leads.extend(page)
            if len(page) < limit:
                break
            offset += limit

        result = []
        for lead in all_leads:
            cat = (lead.get("lead_category") or lead.get("category") or
                   lead.get("status") or lead.get("campaign_status") or "").lower()
            if "interest" not in cat:
                continue
            first = lead.get("first_name", "")
            last  = lead.get("last_name", "")
            email = lead.get("email", "")
            name  = f"{first} {last}".strip() or email
            result.append({"id": lead.get("id"), "name": name, "email": email})

        return jsonify({"ok": True, "leads": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/smartlead/lead-messages")
def lead_messages():
    campaign_id = request.args.get("campaign_id")
    email       = request.args.get("email")
    if not campaign_id or not email:
        return jsonify({"ok": False, "error": "Missing params"}), 400
    try:
        # Try the documented Smartlead message-history path
        raw = sl(f"/campaigns/{campaign_id}/leads/{email}/message-history")
        if isinstance(raw, list):
            msgs = raw
        elif isinstance(raw, dict):
            msgs = raw.get("list", raw.get("data", raw.get("history", raw.get("messages", []))))
            # Some versions wrap in a single key — unwrap if needed
            if not isinstance(msgs, list):
                msgs = [raw]
        else:
            msgs = []
        return jsonify({"ok": True, "messages": msgs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Aimfox Replied Leads ─────────────────────────────────────────────────────

@app.route("/api/aimfox/replied-leads/<campaign_id>")
def af_replied_leads(campaign_id):
    try:
        raw      = af_ws(f"/campaigns/{campaign_id}/audience", {"limit": 200})
        audience = raw.get("audience", raw if isinstance(raw, list) else [])

        result = []
        for a in audience:
            state = (a.get("state") or "").lower()
            if state != "reply":
                continue
            profile = a.get("profile") or a
            first   = profile.get("first_name", a.get("first_name", ""))
            last    = profile.get("last_name",  a.get("last_name",  ""))
            name    = f"{first} {last}".strip() or profile.get("name", a.get("name", ""))
            lid     = a.get("id") or a.get("profile_id") or a.get("lead_id")
            if not name:
                name = f"Lead {lid}"
            result.append({"id": lid, "name": name})

        return jsonify({"ok": True, "leads": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/aimfox/lead-messages")
def af_lead_messages():
    campaign_id = request.args.get("campaign_id")
    lead_id     = request.args.get("lead_id")
    if not campaign_id or not lead_id:
        return jsonify({"ok": False, "error": "Missing params"}), 400
    try:
        # Find conversation for this lead in this campaign
        raw   = af_ws("/conversations", {"campaign_id": campaign_id,
                                          "profile_id": lead_id, "limit": 1})
        convs = raw.get("conversations", raw if isinstance(raw, list) else [])
        if not convs:
            return jsonify({"ok": True, "messages": []})

        conv_id  = convs[0].get("id") or convs[0].get("conversation_id")
        msgs_raw = af_ws(f"/conversations/{conv_id}/messages", {"limit": 50})
        msgs     = msgs_raw if isinstance(msgs_raw, list) \
                   else msgs_raw.get("messages", msgs_raw.get("data", []))
        return jsonify({"ok": True, "messages": msgs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── All Positives (cross-campaign) ───────────────────────────────────────────

@app.route("/api/all-positives")
def all_positives():
    try:
        sl_leads, af_leads = [], []

        # ── Smartlead: all FEAAM campaigns + subsequences ──────────────────
        all_camps = sl("/campaigns")
        if isinstance(all_camps, dict):
            all_camps = all_camps.get("data", [])

        feaam_all = [c for c in all_camps
                     if CLIENT_FILTER.lower() in c.get("name", "").lower()
                     or any(CLIENT_FILTER.lower() in p.get("name", "").lower()
                            for p in all_camps
                            if p["id"] == c.get("parent_campaign_id"))]

        def fetch_sl_camp_positives(c):
            cid   = c["id"]
            cname = c.get("name", f"Campaign {cid}")
            found = []
            offset, limit = 0, 100
            while True:
                p = {"offset": offset, "limit": limit, "api_key": SL_KEY}
                r = requests.get(f"{SL_BASE}/campaigns/{cid}/leads",
                                 params=p, timeout=20)
                if not r.ok:
                    break
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
                        "name":        f"{first} {last}".strip() or email,
                        "email":       email,
                        "campaign":    cname,
                        "campaign_id": cid,
                    })
                if len(page) < limit:
                    break
                offset += limit
            return found

        with ThreadPoolExecutor(max_workers=8) as ex:
            for batch in ex.map(fetch_sl_camp_positives, feaam_all):
                sl_leads.extend(batch)

        # ── Aimfox: all FEAAM-owner campaigns ─────────────────────────────
        target_owner_ids = set(AF_ACCOUNTS.keys())
        af_camps = [c for c in af_ws("/campaigns").get("campaigns", [])
                    if any(str(o) in target_owner_ids for o in c.get("owners", []))]

        def fetch_af_camp_replied(c):
            cid   = c["id"]
            cname = c.get("name", f"Campaign {cid}")
            found = []
            try:
                raw      = af_ws(f"/campaigns/{cid}/audience", {"limit": 500})
                audience = raw.get("audience", raw if isinstance(raw, list) else [])
                for a in audience:
                    if (a.get("state") or "").lower() != "reply":
                        continue
                    profile = a.get("profile") or a
                    first   = profile.get("first_name", a.get("first_name", ""))
                    last    = profile.get("last_name",  a.get("last_name",  ""))
                    name    = f"{first} {last}".strip() or profile.get("name", "")
                    lid     = a.get("id") or a.get("profile_id")
                    found.append({
                        "name":        name or f"Lead {lid}",
                        "id":          lid,
                        "campaign":    cname,
                        "campaign_id": cid,
                    })
            except Exception:
                pass
            return found

        with ThreadPoolExecutor(max_workers=5) as ex:
            for batch in ex.map(fetch_af_camp_replied, af_camps):
                af_leads.extend(batch)

        return jsonify({"ok": True,
                        "smartlead": sl_leads,
                        "aimfox":    af_leads,
                        "fetched_at": datetime.now().isoformat()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Debug ─────────────────────────────────────────────────────────────────────

@app.route("/api/debug/sl-leads/<int:campaign_id>")
def debug_sl_leads(campaign_id):
    """Shows raw Smartlead leads response — use to identify correct field names."""
    p = {"offset": 0, "limit": 5, "api_key": SL_KEY}
    r = requests.get(f"{SL_BASE}/campaigns/{campaign_id}/leads", params=p, timeout=20)
    try:
        body = r.json()
    except Exception:
        body = r.text
    return jsonify({"http_status": r.status_code,
                    "response_type": type(body).__name__,
                    "raw": body})


# ── Health / Index ────────────────────────────────────────────────────────────

@app.route("/api/health")
def health():
    return jsonify({"sl_key": bool(SL_KEY), "af_key": bool(AF_KEY),
                    "filter": CLIENT_FILTER})


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5050))
    app.run(debug=False, port=port, host="0.0.0.0")
