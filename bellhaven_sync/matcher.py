"""Match website locations to CRM accounts and turn findings into proposals.

Matching evidence, strongest first:
  * address  - same normalized street + same 5-digit ZIP
  * phone    - same 10-digit phone, in the same city/state
  * name     - same normalized name, in the same city/state
A name-only match in the same city is "medium" confidence and labeled as such.
Names in a different city or state never match (e.g. "Amberly Manor" in
Hudson, OH is not the CRM's "Amberly Manor" in Colorado Springs, CO).

Proposal kinds:
  ok_update    matched account already under Bellhaven; fix name/status
  reparent     matched account under the wrong parent, no billing block -> move it
  chow         matched account under the wrong parent WITH revenue AND open AR ->
               billing SOP: leave it untouched, create a new account under
               Bellhaven, set chow_current_account on the old one
  chow_link    an extra account for the same facility under another parent that
               has revenue AND open AR -> point it at the surviving account
  duplicate    an extra account for the same facility -> duplicate_of + Inactive
  create       website location with no CRM account
  not_on_site  Bellhaven account no longer on the website -> Needs Review
"""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher

from . import config

STREET_MAP = {
    "street": "st", "st.": "st", "avenue": "ave", "av": "ave", "road": "rd", "drive": "dr",
    "boulevard": "blvd", "lane": "ln", "court": "ct", "place": "pl", "parkway": "pkwy",
    "highway": "hwy", "circle": "cir", "terrace": "ter", "trail": "trl", "way": "way",
    "northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se", "north": "n", "south": "s", "east": "e", "west": "w", "suite": "ste",
}
NAME_MAP = {"rehab": "rehabilitation", "centre": "center", "ctr": "center", "hc": "healthcare"}
NAME_STOP = {"the", "of", "at", "and", "a", "an"}


def norm_street(s: str) -> str:
    s = re.sub(r"[.,#]", " ", (s or "").lower())
    return " ".join(STREET_MAP.get(t, t) for t in s.split())


def norm_name(s: str) -> str:
    s = (s or "").lower().replace("&", " and ").replace("health care", "healthcare")
    s = re.sub(r"\(parent account\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(NAME_MAP.get(t, t) for t in s.split() if NAME_MAP.get(t, t) not in NAME_STOP)


def phone10(s: str) -> str:
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else ""


def name_sim(a: str, b: str) -> float:
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    return max(SequenceMatcher(None, na, nb).ratio(), len(ta & tb) / len(ta | tb))


def same_place(a: dict, b: dict) -> bool:
    return bool(a.get("city")) and a["city"].lower() == (b.get("city") or "").lower() \
        and a.get("state", "").upper() == (b.get("state") or "").upper()


def evidence_for(loc: dict, acc: dict) -> tuple[str, list[str]]:
    """Return (strength, reasons) where strength is strong|medium|none."""
    reasons = []
    addr = bool(loc.get("street")) and loc.get("zip") and loc["zip"] == (acc.get("zip") or "")[:5] \
        and norm_street(loc["street"]) == norm_street(acc.get("street"))
    city = same_place(loc, acc)
    phone = bool(phone10(loc.get("phone"))) and phone10(loc.get("phone")) == phone10(acc.get("phone"))
    exact = norm_name(loc["name"]) == norm_name(acc["name"])
    sim = name_sim(loc["name"], acc["name"])
    if addr:
        reasons.append(f"Same address: {acc.get('street')}, {acc.get('city')}, {acc.get('state')} {acc.get('zip')}")
    if phone and city:
        reasons.append(f"Same phone: {acc.get('phone')}")
    if exact and city:
        reasons.append("Same name (after normalizing punctuation/abbreviations) in the same city")
    elif city and sim >= 0.6:
        reasons.append(f"Similar name in the same city (similarity {sim:.2f})")
    if addr or (phone and city) or (exact and city):
        return "strong", reasons
    if city and sim >= 0.6:
        return "medium", reasons
    return "none", reasons


def find_parent_id(accounts: list[dict]) -> str:
    if config.BELLHAVEN_PARENT_ID:
        return config.BELLHAVEN_PARENT_ID
    refs: dict[str, int] = {}
    for a in accounts:
        if a.get("parent_id"):
            refs[a["parent_id"]] = refs.get(a["parent_id"], 0) + 1
    cands = [a for a in accounts if norm_name(a["name"]).startswith(norm_name(config.PARENT_NAME))
             and not a.get("parent_id")]
    if not cands:
        raise RuntimeError("Could not find the Bellhaven parent account; set BELLHAVEN_PARENT_ID")
    return max(cands, key=lambda a: refs.get(a["id"], 0))["id"]


def is_parent_account(a: dict, parent_ids: set[str]) -> bool:
    return a["id"] in parent_ids or "(parent account)" in a["name"].lower()


def has_billing_block(a: dict) -> bool:
    """SOP: revenue history AND outstanding AR > 0 -> do not re-parent."""
    return a["lifetime_revenue"] > 0 and a["outstanding_ar"] > 0


def care_type_for(loc: dict) -> str:
    for c in loc.get("care_offerings") or []:
        if c.lower() in config.CARE_MAP:
            return config.CARE_MAP[c.lower()]
    return ""


def acc_summary(a: dict, names: dict[str, str]) -> dict:
    return {
        "id": a["id"], "name": a["name"], "parent": names.get(a.get("parent_id") or "", "(none)"),
        "parent_id": a.get("parent_id") or "", "status": a.get("status"),
        "address": f"{a.get('street')}, {a.get('city')}, {a.get('state')} {a.get('zip')}",
        "phone": a.get("phone"), "care_type": a.get("care_type"),
        "lifetime_revenue": a["lifetime_revenue"], "outstanding_ar": a["outstanding_ar"],
        "duplicate_of_account": a.get("duplicate_of_account") or "",
        "chow_current_account": a.get("chow_current_account") or "",
        "crm_url": config.crm_browser_url(a["id"]),
    }


def make_proposal(kind: str, target: str, title: str, changes: dict, before: dict,
                  evidence: dict, confidence: str = "high", create: dict | None = None) -> dict:
    fp_src = json.dumps({"kind": kind, "target": target, "changes": changes, "create": create}, sort_keys=True)
    return {
        "fingerprint": hashlib.sha1(fp_src.encode()).hexdigest()[:16],
        "kind": kind, "target": target, "title": title, "changes": changes, "before": before,
        "create": create, "evidence": evidence, "confidence": confidence,
    }


def build_proposals(locations: list[dict], accounts: list[dict]) -> tuple[list[dict], dict]:
    bh = find_parent_id(accounts)
    parent_ids = {a["parent_id"] for a in accounts if a.get("parent_id")} | {bh}
    names = {a["id"]: a["name"] for a in accounts}
    facilities = [a for a in accounts if not is_parent_account(a, parent_ids)]
    # Accounts already retired by an earlier decision are not candidates for survivor.
    retired = lambda a: bool(a.get("duplicate_of_account") or a.get("chow_current_account"))

    proposals: list[dict] = []
    claimed: set[str] = set()
    stats = {"locations": len(locations), "accounts": len(accounts), "confident_no_change": 0,
             "parent_id": bh}

    for loc in locations:
        site = {k: loc.get(k) for k in ("name", "street", "city", "state", "zip", "phone",
                                        "care_offerings", "administrator", "url")}
        matches = []
        for a in facilities:
            strength, why = evidence_for(loc, a)
            if strength != "none":
                matches.append((strength, why, a))
        strong = [m for m in matches if m[0] == "strong"]
        use = strong or ([m for m in matches if m[0] == "medium"] if len(matches) == 1 else [])
        confidence = "high" if strong else "medium"

        if not use:
            fields = {"name": loc["name"], "street": loc["street"], "city": loc["city"],
                      "state": loc["state"], "zip": loc["zip"], "phone": loc["phone"],
                      "care_type": care_type_for(loc), "parent_id": bh, "status": "Active"}
            ev = {"website": site, "accounts": [acc_summary(m[2], names) for m in matches],
                  "reasons": ["No CRM account has this address, phone, or name in this city."]
                  + ([f"Possible but unconfirmed: {m[2]['name']} ({'; '.join(m[1])})" for m in matches])}
            proposals.append(make_proposal("create", f"loc:{loc['slug']}", f"Create account: {loc['name']}",
                                           {}, {}, ev, "high" if not matches else "low", create=fields))
            continue

        accs = [m[2] for m in use]
        why_by_id = {m[2]["id"]: m[1] for m in use}
        claimed.update(a["id"] for a in accs)
        live = [a for a in accs if not retired(a)] or accs
        survivor = sorted(live, key=lambda a: (
            a.get("parent_id") == bh, (a.get("status") or "") == "Active",
            norm_name(a["name"]) == norm_name(loc["name"]), a["lifetime_revenue"] > 0, a["id"]),
            reverse=True)[0]
        ev_accounts = [acc_summary(a, names) for a in accs]
        reasons = [f"{a['name']} ({a['id']}): " + "; ".join(why_by_id[a["id"]]) for a in accs]
        base_ev = {"website": site, "accounts": ev_accounts, "reasons": reasons}

        # ---- the surviving account --------------------------------------
        if survivor.get("parent_id") == bh:
            changes, before = {}, {}
            if survivor["name"] != loc["name"]:
                changes["name"], before["name"] = loc["name"], survivor["name"]
            if (survivor.get("status") or "") != "Active":
                changes["status"], before["status"] = "Active", survivor.get("status")
            if changes:
                label = "Outdated name" if "name" in changes else "Status fix"
                proposals.append(make_proposal("ok_update", survivor["id"], f"{label}: {survivor['name']} -> {loc['name']}",
                                               changes, before, base_ev, confidence))
            else:
                stats["confident_no_change"] += 1
        elif has_billing_block(survivor):
            fields = {"name": loc["name"], "street": loc["street"] or survivor["street"],
                      "city": loc["city"], "state": loc["state"], "zip": loc["zip"] or survivor["zip"],
                      "phone": loc["phone"] or survivor["phone"],
                      "care_type": survivor.get("care_type") or care_type_for(loc),
                      "parent_id": bh, "status": "Active"}
            ev = dict(base_ev, sop=(f"Lifetime revenue ${survivor['lifetime_revenue']:,.0f} and outstanding AR "
                                     f"${survivor['outstanding_ar']:,.0f} are both > 0, so per billing SOP the old "
                                     f"account stays exactly as is. A new account is created under Bellhaven and "
                                     f"the old account's chow_current_account points to it."))
            proposals.append(make_proposal(
                "chow", survivor["id"],
                f"Change of ownership (billing SOP): {survivor['name']} from {names.get(survivor.get('parent_id') or '', '(none)')} to Bellhaven",
                {"chow_current_account": "<new account id>"},
                {"chow_current_account": survivor.get("chow_current_account") or ""}, ev, confidence, create=fields))
        else:
            changes = {"parent_id": bh}
            before = {"parent_id": survivor.get("parent_id") or ""}
            if survivor["name"] != loc["name"]:
                changes["name"], before["name"] = loc["name"], survivor["name"]
            if (survivor.get("status") or "") != "Active":
                changes["status"], before["status"] = "Active", survivor.get("status")
            ev = dict(base_ev, sop=(f"Lifetime revenue ${survivor['lifetime_revenue']:,.0f}, outstanding AR "
                                     f"${survivor['outstanding_ar']:,.0f}: not both > 0, so re-parenting directly is allowed."))
            proposals.append(make_proposal(
                "reparent", survivor["id"],
                f"Wrong parent: {survivor['name']} from {names.get(survivor.get('parent_id') or '', '(none)')} to Bellhaven",
                changes, before, ev, confidence))

        # ---- extra accounts for the same facility -----------------------
        for a in accs:
            if a["id"] == survivor["id"] or retired(a):
                continue
            if a.get("parent_id") != bh and has_billing_block(a):
                proposals.append(make_proposal(
                    "chow_link", a["id"], f"Old-owner account (billing SOP): {a['name']} -> current {survivor['name']}",
                    {"chow_current_account": survivor["id"]},
                    {"chow_current_account": a.get("chow_current_account") or ""},
                    dict(base_ev, sop=f"Revenue ${a['lifetime_revenue']:,.0f} and AR ${a['outstanding_ar']:,.0f} > 0: "
                                      "keep the old account as is and link it to the current one."), confidence))
            else:
                proposals.append(make_proposal(
                    "duplicate", a["id"], f"Duplicate: {a['name']} ({a['id']}) of {survivor['name']} ({survivor['id']})",
                    {"duplicate_of_account": survivor["id"], "status": "Inactive"},
                    {"duplicate_of_account": a.get("duplicate_of_account") or "", "status": a.get("status")},
                    base_ev, confidence))

    # ---- Bellhaven accounts not on the website --------------------------
    for a in facilities:
        if a.get("parent_id") != bh or a["id"] in claimed or retired(a):
            continue
        if (a.get("status") or "") in ("Inactive", "Needs Review"):
            continue
        other = [o for o in facilities if o["id"] != a["id"] and o.get("parent_id") != bh and not retired(o)
                 and o.get("zip") and o["zip"][:5] == (a.get("zip") or "")[:5]
                 and norm_street(o.get("street")) == norm_street(a.get("street"))]
        ev = {"website": None, "accounts": [acc_summary(x, names) for x in [a] + other],
              "reasons": [f"{a['name']} is under Bellhaven in the CRM but is not listed anywhere on the Bellhaven website."]}
        if other:
            o = other[0]
            ev["reasons"].append(f"Same address as {o['name']} under {names.get(o.get('parent_id') or '', '(none)')}: "
                                 "the facility appears to have changed owner.")
            if has_billing_block(a):
                proposals.append(make_proposal(
                    "chow_link", a["id"], f"Sold (billing SOP): {a['name']} -> current {o['name']}",
                    {"chow_current_account": o["id"]}, {"chow_current_account": a.get("chow_current_account") or ""},
                    dict(ev, sop="Revenue and AR > 0: keep the old account as is, link it to the current one.")))
            else:
                proposals.append(make_proposal(
                    "duplicate", a["id"], f"Sold / duplicate: {a['name']} -> current {o['name']}",
                    {"duplicate_of_account": o["id"], "status": "Inactive"},
                    {"duplicate_of_account": a.get("duplicate_of_account") or "", "status": a.get("status")}, ev))
        else:
            proposals.append(make_proposal(
                "not_on_site", a["id"], f"No longer on website: {a['name']}",
                {"status": "Needs Review"}, {"status": a.get("status")}, ev, "medium"))

    stats["proposals"] = len(proposals)
    return proposals, stats
