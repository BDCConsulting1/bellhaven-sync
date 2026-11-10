"""Scrape every Bellhaven community from the public website.

Discovery crawls the home page, About page and every page of the community
directory, collecting any link shaped like /communities/<slug>. That matters:
at least one community (Findlay) is linked from the home page but not from the
paginated directory.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from . import config

DETAIL_RE = re.compile(r"^/communities/[^/?#]+/?$")
ADDR_RE = re.compile(
    r"(?P<street>\d+[^,\n]*?),\s*(?P<city>[A-Za-z][A-Za-z .'\-]*?),\s*"
    r"(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:-\d{4})?"
)
PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")
LABELS = ("address", "phone", "administrator", "care offerings", "back to", "←", "©", "corporate office")


def _get(session: requests.Session, url: str) -> str:
    for attempt in range(3):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    return ""


def discover_detail_urls(session: requests.Session, base: str = config.BASE_URL, max_pages: int = 60) -> list[str]:
    host = urlparse(base).netloc
    queue = [base + "/", base + "/communities", base + "/about"]
    visited: set[str] = set()
    found: dict[str, None] = {}  # ordered set
    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        soup = BeautifulSoup(_get(session, url), "html.parser")
        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"])
            p = urlparse(full)
            if p.netloc != host:
                continue
            if DETAIL_RE.match(p.path):
                found[f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"] = None
            elif p.path.rstrip("/") in ("", "/communities", "/about"):
                # directory pagination (?page=N) and nav pages
                clean = f"{p.scheme}://{p.netloc}{p.path or '/'}" + (f"?{p.query}" if p.query else "")
                if clean not in visited:
                    queue.append(clean)
    return list(found)


def _lines(soup: BeautifulSoup) -> list[str]:
    return [ln.strip() for ln in soup.get_text("\n").splitlines() if ln.strip()]


CITY_LINE_RE = re.compile(r"^(?P<city>.+?),\s*(?P<state>[A-Z]{2})\.?\s*(?P<zip>\d{5})?(?:-\d{4})?$")


def _parse_dl(soup):
    """Real site layout: <dl class="detail"><dt>Address</dt><dd>street<br/>City, ST ZIP</dd>..."""
    fields = {}
    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd is None:
            continue
        label = dt.get_text(" ", strip=True).lower().rstrip(":")
        if label.startswith("address"):
            parts = [x.strip() for x in dd.get_text("\n").splitlines() if x.strip()]
            if parts:
                m = CITY_LINE_RE.match(parts[-1])
                if m:
                    fields.update(street=", ".join(parts[:-1]), city=m.group("city").strip(),
                                  state=m.group("state"), zip=m.group("zip") or "")
        elif label.startswith("care"):
            fields["care_offerings"] = [b.get_text(" ", strip=True) for b in dd.find_all(["span", "li"])] or \
                [x.strip() for x in re.split(r"[;,\n]", dd.get_text("\n")) if x.strip()]
        elif label.startswith("phone"):
            m = PHONE_RE.search(dd.get_text(" ", strip=True))
            fields["phone"] = m.group(0) if m else dd.get_text(" ", strip=True)
        elif label.startswith("administrator"):
            fields["administrator"] = dd.get_text(" ", strip=True)
    return fields


def parse_detail(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    name = h1.get_text(" ", strip=True) if h1 else ""
    dl = _parse_dl(soup)
    if dl.get("street") and dl.get("city"):
        return {"slug": urlparse(url).path.rstrip("/").split("/")[-1], "url": url, "name": name,
                "street": dl.get("street", ""), "city": dl.get("city", ""), "state": dl.get("state", ""),
                "zip": dl.get("zip", ""), "care_offerings": dl.get("care_offerings", []),
                "phone": dl.get("phone", ""), "administrator": dl.get("administrator", "")}
    # Fallback: free-text layout.
    lines = _lines(soup)

    street = city = state = zip_ = ""
    # Address: first City, ST ZIP match that is not the corporate office footer.
    for i, ln in enumerate(lines):
        if "corporate" in ln.lower():
            continue
        cand = ln
        if ln.lower().rstrip(":") == "address" and i + 1 < len(lines):
            cand = lines[i + 1]
        m = ADDR_RE.search(cand)
        if m:
            street, city, state, zip_ = (m.group("street").strip(), m.group("city").strip(),
                                         m.group("state"), m.group("zip"))
            street = re.sub(r"^address:?\s*", "", street, flags=re.I)
            break

    care: list[str] = []
    for i, ln in enumerate(lines):
        if "care offering" in ln.lower():
            after = ln.split(":", 1)[1].strip() if ":" in ln else ""
            chunks = [after] if after else []
            j = i + 1
            while not after and j < len(lines) and not any(lines[j].lower().startswith(l) for l in LABELS):
                chunks.append(lines[j])
                j += 1
            for c in chunks:
                care += [x.strip(" •-") for x in re.split(r"[;,•]", c) if x.strip(" •-")]
            break

    phone = admin = ""
    for ln in lines:
        low = ln.lower()
        if not phone and "phone" in low and PHONE_RE.search(ln):
            phone = PHONE_RE.search(ln).group(0)
        if not admin and low.startswith("administrator"):
            admin = ln.split(":", 1)[1].strip() if ":" in ln else ""
    if not phone:
        m = PHONE_RE.search("\n".join(lines))
        phone = m.group(0) if m else ""

    return {
        "slug": urlparse(url).path.rstrip("/").split("/")[-1],
        "url": url,
        "name": name,
        "street": street,
        "city": city,
        "state": state,
        "zip": zip_,
        "care_offerings": care,
        "phone": phone,
        "administrator": admin,
    }


def scrape(base: str = config.BASE_URL) -> list[dict]:
    session = requests.Session()
    session.headers["User-Agent"] = "bellhaven-sync/1.0"
    out = []
    for url in discover_detail_urls(session, base):
        loc = parse_detail(_get(session, url), url)
        out.append(loc)
    return out
