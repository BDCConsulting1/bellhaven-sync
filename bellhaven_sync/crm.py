"""Thin client for the Bellhaven CRM sandbox API.

The OpenAPI spec doesn't publish model schemas, so field names are resolved
from the first real record (e.g. `billing_street` vs `street`). Internally the
rest of the code always uses the canonical names on the left of ALIASES.
"""
from __future__ import annotations

import time

import requests

from . import config

ALIASES = {
    "id": ["id", "account_id", "Id"],
    "name": ["name", "account_name", "Name"],
    "parent_id": ["parent_id", "parent_account_id", "ParentId"],
    "status": ["status", "Status"],
    "street": ["billing_street", "street", "address", "billing_address", "address_line1"],
    "city": ["billing_city", "city"],
    "state": ["billing_state", "state"],
    "zip": ["billing_zip", "billing_postal_code", "zip", "postal_code", "zip_code"],
    "phone": ["phone", "Phone"],
    "care_type": ["care_type", "facility_type", "type"],
    "lifetime_revenue": ["lifetime_revenue"],
    "outstanding_ar": ["outstanding_ar"],
    "note": ["note", "notes"],
    "duplicate_of_account": ["duplicate_of_account", "duplicate_of"],
    "chow_current_account": ["chow_current_account"],
    "updated_at": ["updated_at", "last_updated", "last_modified"],
}
LIST_KEYS = ("items", "results", "data", "accounts", "records")


def _num(v) -> float:
    try:
        return float(str(v).replace("$", "").replace(",", "")) if v not in (None, "") else 0.0
    except ValueError:
        return 0.0


class CRM:
    def __init__(self, base: str = config.API_BASE, token: str = config.TOKEN):
        if not token:
            raise SystemExit("BELLHAVEN_TOKEN is not set. Put it in a .env file (see README).")
        self.base = base.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
        self.keymap: dict[str, str] = {}  # canonical -> real API key

    # ---- HTTP -------------------------------------------------------------
    def _req(self, method: str, path: str, **kw):
        for attempt in range(4):
            r = self.s.request(method, self.base + path, timeout=30, **kw)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
            return r.json() if r.content else {}
        raise RuntimeError(f"{method} {path} failed after retries")

    # ---- field mapping ----------------------------------------------------
    def _learn_keys(self, raw: dict) -> None:
        for canon, options in ALIASES.items():
            if canon in self.keymap:
                continue
            for o in options:
                if o in raw:
                    self.keymap[canon] = o
                    break

    def key(self, canon: str) -> str:
        return self.keymap.get(canon, canon)

    def normalize(self, raw: dict) -> dict:
        if "id" not in raw and len(raw) == 1 and isinstance(next(iter(raw.values())), dict):
            raw = next(iter(raw.values()))  # unwrap {"account": {...}}
        self._learn_keys(raw)
        acc = {c: raw.get(self.key(c)) for c in ALIASES}
        for c in ("name", "street", "city", "state", "zip", "phone", "status", "note", "care_type"):
            acc[c] = (acc[c] or "").strip() if isinstance(acc[c], str) or acc[c] is None else str(acc[c])
        acc["lifetime_revenue"] = _num(acc["lifetime_revenue"])
        acc["outstanding_ar"] = _num(acc["outstanding_ar"])
        acc["raw"] = raw
        return acc

    def to_api(self, changes: dict) -> dict:
        return {self.key(k): v for k, v in changes.items()}

    # ---- endpoints --------------------------------------------------------
    def list_accounts(self, page_size: int = 100) -> list[dict]:
        out, page = [], 1
        while True:
            try:
                payload = self._req("GET", "/accounts", params={"page": page, "page_size": page_size})
            except RuntimeError as e:
                if "422" in str(e) and page_size > 50:  # API may cap page_size; fall back to its default
                    page_size, out, page = 50, [], 1
                    continue
                raise
            if isinstance(payload, list):
                items, meta = payload, {}
            else:
                key = next((k for k in LIST_KEYS if isinstance(payload.get(k), list)), None)
                if key is None:
                    raise RuntimeError(f"Unexpected list response keys: {list(payload)}")
                items, meta = payload[key], payload
            out += [self.normalize(i) for i in items]
            pages = meta.get("pages") or meta.get("total_pages")
            total = meta.get("total") or meta.get("count")
            if not items or (pages and page >= int(pages)) or (total and len(out) >= int(total)) \
                    or len(items) < page_size:
                break
            page += 1
        return out

    def get_account(self, account_id: str) -> dict:
        return self.normalize(self._req("GET", f"/accounts/{account_id}"))

    def update_account(self, account_id: str, changes: dict) -> dict:
        return self.normalize(self._req("PATCH", f"/accounts/{account_id}", json=self.to_api(changes)))

    def create_account(self, fields: dict) -> dict:
        return self.normalize(self._req("POST", "/accounts", json=self.to_api(fields)))
