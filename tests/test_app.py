"""Smoke test: the review page renders proposals and Reject works."""
from tests.test_end_to_end import env, run_sync  # noqa: F401


def test_review_page(env):
    app_fake, base = env
    run_sync(base)
    import app as review
    c = review.app.test_client()
    html = c.get("/").get_data(as_text=True)
    assert "Change of ownership" in html and "Billing SOP" in html and "Approve" in html
    from bellhaven_sync import store
    con = store.connect()
    pid = con.execute("SELECT id FROM proposals WHERE kind='not_on_site'").fetchone()[0]
    r = c.post(f"/proposals/{pid}/reject", data={"note": "checking with sales"})
    assert r.status_code == 302 and store.get(con, pid)["status"] == "rejected"
    pid2 = con.execute("SELECT id FROM proposals WHERE kind='reparent'").fetchone()[0]
    c.post(f"/proposals/{pid2}/approve", data={})
    assert store.get(con, pid2)["status"] == "applied"
    assert "Decided" in c.get("/?view=history").get_data(as_text=True)
