"""Daily pipeline: scrape website -> pull CRM -> match -> queue proposals.

Never writes to the CRM. Writes only happen from the review app after approval.

    python sync.py
"""
from __future__ import annotations

import json
from datetime import date

from bellhaven_sync import config, store
from bellhaven_sync.crm import CRM
from bellhaven_sync.matcher import build_proposals
from bellhaven_sync.scraper import scrape


def main() -> None:
    con = store.connect()
    run_id = store.start_run(con)

    locations = scrape()
    incomplete = [l["name"] or l["url"] for l in locations if not (l["street"] and l["zip"] and l["city"])]
    print(f"Scraped {len(locations)} website locations")
    if incomplete:
        print(f"  WARNING: incomplete address for: {', '.join(incomplete)}")
    if len(incomplete) > max(2, len(locations) // 10):
        store.finish_run(con, run_id, {"aborted": "scrape looks broken", "incomplete": incomplete})
        raise SystemExit("Too many locations without an address: the site layout probably changed. "
                         "No proposals were queued. Fix the scraper and re-run.")

    crm = CRM()
    accounts = crm.list_accounts()
    print(f"Pulled {len(accounts)} CRM accounts (field map: {crm.keymap})")

    # Keep a dated snapshot of inputs for audit/debugging.
    snap = config.DATA_DIR / f"snapshot-{date.today().isoformat()}.json"
    snap.write_text(json.dumps({"locations": locations,
                                "accounts": [{k: v for k, v in a.items() if k != "raw"} for a in accounts]}, indent=2))

    proposals, stats = build_proposals(locations, accounts)
    counts: dict[str, int] = {}
    for p in proposals:
        counts[p["kind"]] = counts.get(p["kind"], 0) + 1
    result = store.upsert_proposals(con, run_id, proposals)
    summary = {**stats, "by_kind": counts, **result}
    store.finish_run(con, run_id, summary)

    print(f"Confident matches needing no change: {stats['confident_no_change']}")
    for k, v in sorted(counts.items()):
        print(f"  {k:12} {v}")
    print(f"New proposals queued: {result['new']} | already known (not re-proposed): "
          f"{result['already_known']} | superseded: {result['superseded']}")
    print("Review them with:  python app.py   then open http://127.0.0.1:5000")


if __name__ == "__main__":
    main()
