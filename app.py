"""Local review app. Nothing writes to the CRM until a reviewer clicks Approve.

    python app.py      -> http://127.0.0.1:5000
"""
from __future__ import annotations

from flask import Flask, redirect, render_template_string, request, url_for

from bellhaven_sync import config, store
from bellhaven_sync.apply import Conflict, apply
from bellhaven_sync.crm import CRM

app = Flask(__name__)
ORDER = ["chow", "reparent", "chow_link", "duplicate", "ok_update", "create", "not_on_site"]
LABELS = {
    "chow": "Change of ownership (billing SOP: new account + CHOW link)",
    "reparent": "Wrong parent: move to Bellhaven",
    "chow_link": "Old-owner account: link to current (billing SOP)",
    "duplicate": "Duplicate: mark Inactive + duplicate_of",
    "ok_update": "Matched under Bellhaven: name/status fix",
    "create": "On website, missing from CRM: create",
    "not_on_site": "Under Bellhaven in CRM, gone from website: Needs Review",
}

PAGE = """
<!doctype html><html><head><meta charset="utf-8"><title>Bellhaven sync review</title>
<style>
 body{font:14px/1.45 -apple-system,Segoe UI,Arial,sans-serif;margin:0;background:#f6f4ef;color:#222}
 header{background:#3b1d24;color:#fff;padding:14px 24px;display:flex;gap:24px;align-items:center}
 header a{color:#f3c9d1;text-decoration:none} main{max-width:1150px;margin:0 auto;padding:20px}
 h2{margin:28px 0 8px;font-size:17px} .card{background:#fff;border:1px solid #e3ddd2;border-radius:8px;padding:14px 16px;margin:10px 0}
 .title{font-weight:600;font-size:15px} .meta{color:#777;font-size:12px}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:10px}
 .box{background:#faf8f4;border:1px solid #eee5d8;border-radius:6px;padding:8px 10px;font-size:13px}
 table{border-collapse:collapse;font-size:13px} td,th{padding:3px 8px;border-bottom:1px solid #eee;text-align:left;vertical-align:top}
 .old{color:#a33;text-decoration:line-through} .new{color:#176b2c;font-weight:600}
 .sop{background:#fff7d6;border-left:4px solid #e2c13a;padding:6px 10px;margin-top:8px;font-size:13px}
 .btn{border:0;border-radius:5px;padding:7px 14px;font-weight:600;cursor:pointer}
 .approve{background:#1f7a3a;color:#fff} .reject{background:#ddd}
 .flash{background:#e7f4ea;border:1px solid #9cc9a8;padding:10px;border-radius:6px}
 .flash.err{background:#fbe9e9;border-color:#d99}
 .pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;background:#eee;margin-left:6px}
 .low{background:#fde2c8} .medium{background:#fff1b8} .high{background:#d9f0dd}
 input[type=text]{width:260px;padding:5px}
</style></head><body>
<header><strong>Bellhaven sync review</strong>
 <a href="{{ url_for('index') }}">Pending ({{ counts.pending }})</a>
 <a href="{{ url_for('index', view='history') }}">Decided ({{ counts.decided }})</a>
 <a href="{{ base }}/crm/{{ token }}/accounts" target="_blank">CRM browser ↗</a></header>
<main>
{% if msg %}<div class="flash {{ 'err' if err else '' }}">{{ msg }}</div>{% endif %}
{% for kind, items in groups %}
 <h2>{{ labels.get(kind, kind) }} ({{ items|length }})</h2>
 {% for p in items %}
 <div class="card" id="p{{ p.id }}">
  <div class="title">{{ p.title }} <span class="pill {{ p.confidence }}">{{ p.confidence }} confidence</span>
   {% if p.status != 'pending' %}<span class="pill">{{ p.status }}</span>{% endif %}</div>
  <div class="meta">#{{ p.id }} · target {{ p.target }} · first seen run {{ p.first_seen_run }}</div>
  <table style="margin-top:8px">
   <tr><th>Field</th><th>Current</th><th>Proposed</th></tr>
   {% for k, v in (p.changes or {}).items() %}
   <tr><td>{{ k }}</td><td class="old">{{ (p.before or {}).get(k) or '—' }}</td><td class="new">{{ v }}</td></tr>
   {% endfor %}
   {% if p.create_fields %}{% for k, v in p.create_fields.items() %}
   <tr><td>new account · {{ k }}</td><td>—</td><td class="new">{{ v }}</td></tr>{% endfor %}{% endif %}
  </table>
  {% if p.evidence.sop %}<div class="sop"><b>Billing SOP:</b> {{ p.evidence.sop }}</div>{% endif %}
  <div class="grid">
   <div class="box"><b>Website</b><br>
    {% if p.evidence.website %}{% set w = p.evidence.website %}
     {{ w.name }}<br>{{ w.street }}, {{ w.city }}, {{ w.state }} {{ w.zip }}<br>
     Phone {{ w.phone or '—' }} · {{ (w.care_offerings or [])|join('; ') }}<br>
     Admin {{ w.administrator or '—' }} · <a href="{{ w.url }}" target="_blank">listing ↗</a>
    {% else %}<i>Not listed on the website.</i>{% endif %}
   </div>
   <div class="box"><b>CRM</b>
    {% for a in p.evidence.accounts %}<div style="margin-top:4px">
     <a href="{{ a.crm_url }}" target="_blank">{{ a.name }}</a> <span class="meta">{{ a.id }}</span><br>
     Parent: {{ a.parent }} · {{ a.status }} · {{ a.care_type }}<br>{{ a.address }} · {{ a.phone }}<br>
     Revenue ${{ '{:,.0f}'.format(a.lifetime_revenue) }} · AR ${{ '{:,.0f}'.format(a.outstanding_ar) }}
     {% if a.duplicate_of_account %}· dup of {{ a.duplicate_of_account }}{% endif %}
     {% if a.chow_current_account %}· CHOW → {{ a.chow_current_account }}{% endif %}</div>
    {% else %}<i>No matching account.</i>{% endfor %}
   </div>
  </div>
  <ul style="margin:8px 0 0 18px">{% for r in p.evidence.reasons %}<li>{{ r }}</li>{% endfor %}</ul>
  {% if p.status in ('pending', 'failed', 'conflict') %}
  <form method="post" style="margin-top:10px;display:flex;gap:8px;align-items:center">
   <input type="text" name="note" placeholder="Reviewer note (optional)">
   <button class="btn approve" formaction="{{ url_for('decide', pid=p.id, action='approve') }}">Approve & write to CRM</button>
   <button class="btn reject" formaction="{{ url_for('decide', pid=p.id, action='reject') }}">Reject</button>
  </form>
  {% else %}<div class="meta" style="margin-top:6px">Decided {{ p.decided_at }}
   {% if p.reviewer_note %}· note: {{ p.reviewer_note }}{% endif %}{% if p.result %}· result: {{ p.result }}{% endif %}</div>{% endif %}
 </div>
 {% endfor %}
{% else %}<p>Nothing here. Run <code>python sync.py</code> to look for changes.</p>{% endfor %}
</main></body></html>
"""


@app.route("/")
def index():
    con = store.connect()
    view = request.args.get("view", "pending")
    if view == "history":
        rows = con.execute("SELECT * FROM proposals WHERE status NOT IN ('pending') ORDER BY decided_at DESC").fetchall()
    else:
        rows = con.execute("SELECT * FROM proposals WHERE status IN ('pending','failed','conflict') ORDER BY id").fetchall()
    items = [store.load(r) for r in rows]
    groups = [(k, [p for p in items if p["kind"] == k]) for k in ORDER]
    groups = [g for g in groups if g[1]]
    counts = {
        "pending": con.execute("SELECT COUNT(*) FROM proposals WHERE status IN ('pending','failed','conflict')").fetchone()[0],
        "decided": con.execute("SELECT COUNT(*) FROM proposals WHERE status NOT IN ('pending')").fetchone()[0],
    }
    return render_template_string(PAGE, groups=groups, labels=LABELS, counts=counts, base=config.BASE_URL,
                                  token=config.TOKEN, msg=request.args.get("msg"), err=request.args.get("err"))


@app.post("/proposals/<int:pid>/<action>")
def decide(pid: int, action: str):
    con = store.connect()
    p = store.get(con, pid)
    note = request.form.get("note", "")
    if p["status"] not in ("pending", "failed", "conflict"):
        return redirect(url_for("index", msg=f"#{pid} was already {p['status']}."))
    if action == "reject":
        store.set_status(con, pid, "rejected", note=note)
        return redirect(url_for("index", msg=f"Rejected #{pid}. It will not be proposed again."))
    try:
        result = apply(CRM(), p)
        store.set_status(con, pid, "applied", result=result, note=note)
        return redirect(url_for("index", msg=f"Approved #{pid} and wrote to CRM: {result}"))
    except Conflict as e:
        store.set_status(con, pid, "conflict", result={"error": str(e)}, note=note)
        return redirect(url_for("index", msg=f"#{pid} not written: {e}", err=1))
    except Exception as e:  # noqa: BLE001 - show any API error to the reviewer
        store.set_status(con, pid, "failed", result={"error": str(e)}, note=note)
        return redirect(url_for("index", msg=f"#{pid} failed: {e}", err=1))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
