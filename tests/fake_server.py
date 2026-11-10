"""A tiny stand-in for the Bellhaven website + CRM API, used only by tests.

It reproduces the tricky shapes seen in the real sandbox: paginated directory,
a homepage-only community, renamed facilities, wrong parents, duplicates,
a billing-SOP account, a same-name facility in another state, and a sold one.
"""
from __future__ import annotations

import copy
import itertools

from flask import Flask, jsonify, request

BH, CT, HV, JP, ML = "P_BH", "P_CT", "P_HV", "P_JP", "P_ML"

LOCATIONS = [  # slug, name, street, city, st, zip, care, phone
    ("bellhaven-of-wooster", "Bellhaven of Wooster", "100 Main Street", "Wooster", "OH", "44691", "Short-Term Rehabilitation & Nursing", "(330) 555-0100"),
    ("bellhaven-rehabilitation-nursing-of-grove-city", "Bellhaven Rehabilitation & Nursing of Grove City", "200 Stringtown Rd", "Grove City", "OH", "43123", "Short-Term Rehabilitation & Nursing", "(614) 555-0200"),
    ("bellhaven-of-tiffin", "Bellhaven of Tiffin", "300 Market St", "Tiffin", "OH", "44883", "Short-Term Rehabilitation & Nursing", "(419) 555-0300"),
    ("bellhaven-of-marietta", "Bellhaven of Marietta", "805 Colegate Drive", "Marietta", "OH", "45750", "Short-Term Rehabilitation & Nursing", "(740) 555-0400"),
    ("bellhaven-of-owosso", "Bellhaven of Owosso", "500 E Main St", "Owosso", "MI", "48867", "Assisted Living", "(989) 555-0500"),
    ("bellhaven-shores-of-erie", "Bellhaven Shores of Erie", "600 Peninsula Dr", "Erie", "PA", "16505", "Assisted Living; Memory Support", "(814) 555-0600"),
    ("bellhaven-of-port-clinton", "Bellhaven of Port Clinton", "700 Lakeshore Dr", "Port Clinton", "OH", "43452", "Short-Term Rehabilitation & Nursing", "(419) 555-0700"),
    ("bellhaven-of-batavia", "Bellhaven of Batavia", "800 Old State Rt 32", "Batavia", "OH", "45103", "Short-Term Rehabilitation & Nursing", "(513) 555-0800"),
    ("amberly-manor", "Amberly Manor", "900 Darrow Rd", "Hudson", "OH", "44236", "Assisted Living", "(330) 555-0900"),
    ("bellhaven-of-kettering", "Bellhaven of Kettering", "1000 Shroyer Road", "Kettering", "OH", "45429", "Short-Term Rehabilitation & Nursing", "(937) 555-1000"),
    ("bellhaven-willow-creek", "Bellhaven Willow Creek", "1100 Romence Rd", "Portage", "MI", "49024", "Memory Support", "(269) 555-1100"),
    ("bellhaven-meadows-of-findlay", "Bellhaven Meadows of Findlay", "1800 N Blanchard St", "Findlay", "OH", "45840", "Assisted Living; Memory Support", "(231) 533-2969"),
]
HOMEPAGE_ONLY = "bellhaven-meadows-of-findlay"

_ids = (f"A{n:03d}" for n in itertools.count(1))


def acct(name, parent, street, city, st, zip_, care="Skilled Nursing", status="Active", rev=0, ar=0, phone=""):
    return {"id": next(_ids), "name": name, "parent_id": parent, "billing_street": street, "billing_city": city,
            "billing_state": st, "billing_zip": zip_, "care_type": care, "status": status, "phone": phone,
            "lifetime_revenue": rev, "outstanding_ar": ar, "note": None,
            "duplicate_of_account": None, "chow_current_account": None}


def seed():
    global _ids
    _ids = (f"A{n:03d}" for n in itertools.count(1))
    parents = [
        {**acct("Bellhaven Senior Living (Parent Account)", None, "", "", "", ""), "id": BH},
        {**acct("Cedar Trail Communities (Parent Account)", None, "", "", "", ""), "id": CT},
        {**acct("Harborview Care Group (Parent Account)", None, "", "", "", ""), "id": HV},
        {**acct("Juniper Point Healthcare (Parent Account)", None, "", "", "", ""), "id": JP},
        {**acct("Millstone Health Partners (Parent Account)", None, "", "", "", ""), "id": ML},
    ]
    accs = [
        acct("Bellhaven of Wooster", BH, "100 Main St.", "Wooster", "OH", "44691"),                       # clean
        acct("Bellhaven Rehab and Nursing of Grove City", BH, "200 Stringtown Road", "Grove City", "OH", "43123"),  # name variant
        acct("Bellhaven of Tiffin", CT, "300 Market Street", "Tiffin", "OH", "44883", rev=12000, ar=0),   # reparent
        acct("Bellhaven of Marietta", CT, "805 Colegate Drive", "Marietta", "OH", "45750", rev=51250, ar=3800),  # CHOW
        acct("Bellhaven of Owosso", BH, "500 E Main St", "Owosso", "MI", "48867", "Assisted Living", rev=900),
        acct("Bellhaven of Owosso", BH, "500 East Main Street", "Owosso", "MI", "48867", "Assisted Living"),  # dup
        acct("Bellhaven Shores of Erie", BH, "600 Peninsula Drive", "Erie", "PA", "16505", "Assisted Living"),
        acct("Harborview Shores of Erie", HV, "600 Peninsula Dr", "Erie", "PA", "16505", "Assisted Living", rev=4000, ar=250),  # chow_link
        acct("Bellhaven of Port Clinton", BH, "700 Lakeshore Drive", "Port Clinton", "OH", "43452"),
        acct("Harborview Nursing & Rehab of Port Clinton", HV, "700 Lakeshore Dr", "Port Clinton", "OH", "43452", rev=0, ar=0),  # dup
        acct("Amberly Manor", JP, "1 Pikes Peak Ave", "Colorado Springs", "CO", "80903", "Assisted Living"),  # trap
        acct("Kettering Care Centre", HV, "55 Other Ave", "Kettering", "OH", "45440"),
        acct("Kettering Nursing & Rehabilitation", None, "1000 Shroyer Rd", "Kettering", "OH", "45429"),  # the match
        acct("Kettering Senior Campus", CT, "77 Far Rd", "Kettering", "OH", "45420"),
        acct("Sunny Acres Retirement Home", BH, "1100 Romence Road", "Portage", "MI", "49024", "Memory Care"),  # renamed
        acct("Bellhaven Meadows of Findlay", None, "1800 North Blanchard Street", "Findlay", "OH", "45840", "Assisted Living"),
        acct("Bellhaven of Coldwater", BH, "12 Chicago St", "Coldwater", "MI", "49036", "Assisted Living"),  # gone
        acct("Bellhaven of Sandusky", BH, "44 Perkins Ave", "Sandusky", "OH", "44870"),  # sold
        acct("Millstone Care of Sandusky", ML, "44 Perkins Avenue", "Sandusky", "OH", "44870"),
    ]
    return {a["id"]: a for a in parents + accs}


def out(a):
    """Shape a record like the real API: account_id instead of id, empty strings not nulls."""
    d = {("account_id" if k == "id" else k): ("" if v is None else v) for k, v in a.items()}
    return copy.deepcopy(d)


def create_app():
    app = Flask(__name__)
    app.config["DB"] = seed()
    db = lambda: app.config["DB"]
    nav = '<nav><a href="/">Home</a> <a href="/communities">Our Communities</a> <a href="/about">About Us</a></nav>'
    foot = "<footer>© 2026 Bellhaven Senior Living · Corporate Office: 400 Bellhaven Way, Columbus, OH</footer>"

    @app.get("/")
    def home():
        return f"<html><body>{nav}<h1>Care that feels like family.</h1><p><b>New this year</b> " \
               f'<a href="/communities/{HOMEPAGE_ONLY}">Bellhaven Meadows of Findlay</a></p>{foot}</body></html>'

    @app.get("/about")
    def about():
        return f"<html><body>{nav}<h1>About</h1>{foot}</body></html>"

    @app.get("/communities")
    def directory():
        listed = [l for l in LOCATIONS if l[0] != HOMEPAGE_ONLY]
        page = int(request.args.get("page", 1))
        chunk = listed[(page - 1) * 5: page * 5]
        cards = "".join(f'<div class="card"><a href="/communities/{l[0]}">{l[1]}</a> {l[3]}, {l[4]}</div>' for l in chunk)
        nxt = f'<a href="/communities?page={page + 1}">Next</a>' if page * 5 < len(listed) else ""
        return f"<html><body>{nav}<h1>Our Communities</h1>{cards}{nxt}{foot}</body></html>"

    @app.get("/communities/<slug>")
    def detail(slug):
        l = next(x for x in LOCATIONS if x[0] == slug)
        return (f"<html><body>{nav}<h1>{l[1]}</h1><p><strong>Address</strong><br>{l[2]}, {l[3]}, {l[4]} {l[5]}</p>"
                f"<p><strong>Care Offerings:</strong> {l[6]}</p><p>Administrator: Pat Doe</p>"
                f"<p>Phone: {l[7]}</p><a href='/communities'>← Back to all communities</a>{foot}</body></html>")

    @app.get("/api/v1/accounts")
    def list_accounts():
        rows = sorted(db().values(), key=lambda a: a["name"])
        page, size = int(request.args.get("page", 1)), int(request.args.get("page_size", 50))
        return jsonify({"data": [out(a) for a in rows[(page - 1) * size: page * size]], "total": len(rows),
                        "page": page, "page_size": size})

    @app.get("/api/v1/accounts/<aid>")
    def get_account(aid):
        return jsonify(out(db()[aid]))

    @app.patch("/api/v1/accounts/<aid>")
    def patch(aid):
        body = request.get_json()
        if "status" in body and body["status"] not in ("Active", "Inactive", "Needs Review"):
            return jsonify({"detail": "bad status"}), 422
        db()[aid].update(body)
        return jsonify(out(db()[aid]))

    @app.post("/api/v1/accounts")
    def create():
        body = request.get_json()
        new = acct(body["name"], body.get("parent_id"), body.get("billing_street", ""), body.get("billing_city", ""),
                   body.get("billing_state", ""), body.get("billing_zip", ""))
        new.update(body)
        new["id"] = "N" + str(len(db()))
        db()[new["id"]] = new
        return jsonify(out(new)), 201

    return app
