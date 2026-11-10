"""End-to-end test against the fake server: scrape -> match -> approve all -> re-run.

    python -m pytest -q
"""
from __future__ import annotations

import threading

import pytest
from werkzeug.serving import make_server

from tests import fake_server as fs


@pytest.fixture()
def env(tmp_path, monkeypatch):
    app = fs.create_app()
    srv = make_server("127.0.0.1", 0, app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    from bellhaven_sync import config
    monkeypatch.setattr(config, "BASE_URL", base)
    monkeypatch.setattr(config, "API_BASE", base + "/api/v1")
    monkeypatch.setattr(config, "TOKEN", "test")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "sync.db")
    yield app, base
    srv.shutdown()


def run_sync(base):
    from bellhaven_sync import scraper, store
    from bellhaven_sync.crm import CRM
    from bellhaven_sync.matcher import build_proposals
    locs = scraper.scrape(base)
    crm = CRM(base + "/api/v1", "test")
    props, stats = build_proposals(locs, crm.list_accounts())
    con = store.connect()
    rid = store.start_run(con)
    return locs, props, store.upsert_proposals(con, rid, props), con


def by_name(db, name):
    return [a for a in db.values() if a["name"] == name]


def test_pipeline(env):
    app, base = env
    db = app.config["DB"]
    locs, props, res, con = run_sync(base)

    assert len(locs) == 12, "homepage-only Findlay must be found too"
    findlay = next(l for l in locs if "Findlay" in l["name"])
    assert findlay["zip"] == "45840" and findlay["care_offerings"] == ["Assisted Living", "Memory Support"]

    kinds = {}
    for p in props:
        kinds.setdefault(p["kind"], []).append(p)
    titles = {p["kind"] + ":" + p["title"] for p in props}
    print("\n".join(sorted(titles)))

    assert any("Marietta" in p["title"] for p in kinds["chow"])
    assert not any("Marietta" in p["title"] for p in kinds.get("reparent", []))
    rep = {p["title"] for p in kinds["reparent"]}
    assert any("Tiffin" in t for t in rep) and any("Kettering Nursing" in t for t in rep) and any("Findlay" in t for t in rep)
    assert {p["create"]["name"] for p in kinds["create"]} == {"Bellhaven of Batavia", "Amberly Manor"}
    assert any("Harborview Shores" in p["title"] for p in kinds["chow_link"])
    dups = " ".join(p["title"] for p in kinds["duplicate"])
    assert "Owosso" in dups and "Harborview Nursing" in dups and "Sandusky" in dups
    assert [p["title"] for p in kinds["not_on_site"]] == ["No longer on website: Bellhaven of Coldwater"]
    renames = " ".join(p["title"] for p in kinds["ok_update"])
    assert "Sunny Acres" in renames and "Grove City" in renames

    # Approve everything through the same code path the review app uses.
    from bellhaven_sync import store
    from bellhaven_sync.apply import apply
    from bellhaven_sync.crm import CRM
    crm = CRM(base + "/api/v1", "test")
    reject_id = None
    for row in con.execute("SELECT id, kind, title FROM proposals WHERE status='pending'").fetchall():
        if row["kind"] == "not_on_site":
            store.set_status(con, row["id"], "rejected")
            reject_id = row["id"]
            continue
        p = store.get(con, row["id"])
        store.set_status(con, row["id"], "applied", result=apply(crm, p))

    # SOP: Marietta old account untouched except the CHOW link; new account under Bellhaven.
    old = by_name(db, "Bellhaven of Marietta")
    old_acc = next(a for a in old if a["parent_id"] == fs.CT)
    new_acc = next(a for a in old if a["parent_id"] == fs.BH)
    assert old_acc["chow_current_account"] == new_acc["id"] and not old_acc["note"]
    assert by_name(db, "Bellhaven of Tiffin")[0]["parent_id"] == fs.BH
    assert by_name(db, "Bellhaven Willow Creek")[0]["parent_id"] == fs.BH
    assert by_name(db, "Amberly Manor")[0]["parent_id"] == fs.JP  # CO account untouched
    assert len(by_name(db, "Amberly Manor")) == 2
    hv = by_name(db, "Harborview Shores of Erie")[0]
    assert hv["chow_current_account"] and hv["parent_id"] == fs.HV and hv["status"] == "Active"

    # Re-run: nothing new, nothing re-proposed (including the rejected one).
    _, props2, res2, con2 = run_sync(base)
    assert res2["new"] == 0, [p["title"] for p in props2]
    assert store.get(con2, reject_id)["status"] == "rejected"
