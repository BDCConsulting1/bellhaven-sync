"""Write an approved proposal to the CRM, with safety checks.

Before writing: re-fetch the account and confirm the fields still hold the
values the reviewer saw ("before"). If someone changed them since, stop and
mark the proposal 'conflict' instead of overwriting. Re-check the billing SOP
at write time too. After writing: re-fetch and confirm the change landed.
"""
from __future__ import annotations

from datetime import date

from .crm import CRM
from .matcher import has_billing_block, norm_street

TAG = "[bellhaven-sync]"


class Conflict(Exception):
    pass


def _note(existing: str, text: str) -> str:
    line = f"{TAG} {date.today().isoformat()}: {text}"
    return f"{existing}\n{line}".strip() if existing else line


def _check_before(acc: dict, before: dict) -> None:
    for k, v in (before or {}).items():
        cur = acc.get(k) or ""
        if str(cur) != str(v or ""):
            raise Conflict(f"{k} changed since this was proposed (was {v!r}, now {cur!r}). Re-run sync.")


def _verify(crm: CRM, account_id: str, expected: dict) -> dict:
    acc = crm.get_account(account_id)
    bad = {k: acc.get(k) for k, v in expected.items() if str(acc.get(k) or "") != str(v)}
    if bad:
        raise RuntimeError(f"Write did not land on {account_id}: {bad}")
    return acc


def _find_existing_new(crm: CRM, fields: dict) -> str | None:
    """Idempotency for creates: an account at this address under the target parent already exists?"""
    for a in crm.list_accounts():
        if a.get("parent_id") == fields["parent_id"] and (a.get("zip") or "")[:5] == fields["zip"] \
                and norm_street(a.get("street")) == norm_street(fields["street"]):
            return a["id"]
    return None


def apply(crm: CRM, p: dict) -> dict:
    kind, target = p["kind"], p["target"]

    if kind == "create":
        fields = dict(p["create_fields"])
        existing = _find_existing_new(crm, fields)
        if existing:
            return {"created": existing, "note": "Account already existed; nothing created."}
        fields["note"] = _note("", f"Created from Bellhaven website listing {p['evidence']['website']['url']}")
        new = crm.create_account({k: v for k, v in fields.items() if v not in (None, "")})
        _verify(crm, new["id"], {"parent_id": fields["parent_id"], "name": fields["name"]})
        return {"created": new["id"]}

    acc = crm.get_account(target)
    _check_before(acc, p["before"])

    if kind == "chow":
        if not has_billing_block(acc):
            raise Conflict("Revenue/AR no longer both > 0; SOP now says re-parent directly. Re-run sync.")
        fields = dict(p["create_fields"])
        new_id = (p.get("result") or {}).get("created") or _find_existing_new(crm, fields)
        if not new_id:
            fields["note"] = _note("", f"Created for change of ownership; prior account {target} kept for billing.")
            new_id = crm.create_account({k: v for k, v in fields.items() if v not in (None, "")})["id"]
        # Old account: ONLY chow_current_account changes (SOP: leave it exactly as is).
        crm.update_account(target, {"chow_current_account": new_id})
        _verify(crm, target, {"chow_current_account": new_id, "parent_id": acc.get("parent_id") or ""})
        return {"created": new_id, "updated": target}

    changes = dict(p["changes"])
    if kind == "reparent" and has_billing_block(acc):
        raise Conflict("Account now has revenue AND open AR; SOP requires a CHOW, not a re-parent. Re-run sync.")
    if kind == "chow_link":
        crm.update_account(target, changes)  # SOP: touch nothing else on the old account
        _verify(crm, target, changes)
        return {"updated": target}

    reason = {
        "reparent": "Moved to Bellhaven per Bellhaven website listing",
        "ok_update": "Name/status aligned with Bellhaven website",
        "duplicate": f"Duplicate of {changes.get('duplicate_of_account')}",
        "not_on_site": "No longer listed on Bellhaven website; owner unknown",
    }.get(kind, kind)
    changes["note"] = _note(acc.get("note") or "", reason)
    crm.update_account(target, changes)
    _verify(crm, target, {k: v for k, v in changes.items() if k != "note"})
    return {"updated": target}
