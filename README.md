# bellhaven-sync

Keeps the CRM's picture of which Bellhaven Senior Living communities belong to
the Bellhaven parent account accurate as facilities are bought, sold and renamed.

```
website ──scrape──┐
                  ├─ match ─> proposals (SQLite) ─> review app ─approve─> CRM API
CRM API ──pull────┘                                       └─reject─> remembered, never re-proposed
```

## Setup (Mac)

```bash
cd bellhaven-sync
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your token in .env
```

## Run

```bash
python sync.py      # scrape + match + queue proposals. Never writes to the CRM.
python app.py       # review UI at http://127.0.0.1:5000. Approve writes; reject is remembered.
python -m pytest -q # end-to-end test against a local fake of the site + API
```

Scheduling: `crontab.txt` (daily at 6:15 AM) or `.github/workflows/daily-sync.yml`.

## How matching works

1. **Scrape.** Crawl the home page, About page and every page of the community
   directory, collecting every `/communities/<slug>` link. Then read each
   community page for name, street, city, state, ZIP, phone and care offerings.
   The home page matters: Bellhaven Meadows of Findlay is linked only there.
2. **Match** each website location to CRM facility accounts (parent accounts excluded):
   - **strong:** same normalized street + ZIP, or same phone in the same city,
     or same normalized name in the same city ("Rehab and" = "Rehabilitation &",
     "Centre" = "Center", "Health Care" = "Healthcare").
   - **medium:** a single similar-name account in the same city. Labeled medium in the UI.
   - Never match across cities/states (the CRM's Amberly Manor in Colorado
     Springs is not the website's Amberly Manor in Hudson, OH).
3. **Classify** into proposals:

| Situation | Proposal |
| --- | --- |
| Under Bellhaven, name/status differs from site | Update name / set Active |
| Under another parent (or none), not (revenue > 0 AND AR > 0) | Re-parent to Bellhaven (+ name) |
| Under another parent, revenue > 0 AND AR > 0 | **Billing SOP:** leave old account as is, create new account under Bellhaven, set `chow_current_account` on old |
| Extra account for the same facility | `duplicate_of_account` = survivor, status Inactive |
| Extra account under old owner with revenue AND AR | Link with `chow_current_account` instead of marking duplicate |
| On site, no CRM account | Create under Bellhaven |
| Under Bellhaven, not on site, same address as another owner's account | Duplicate of / CHOW link to that account |
| Under Bellhaven, not on site, no other trace | Status Needs Review (owner unknown, so no guessing) |

Survivor for duplicates: already under Bellhaven, then Active, then exact name, then has revenue.

## Safety

- Nothing writes without approval. `sync.py` is read-only against the CRM.
- **Re-runs are safe.** Each proposal has a fingerprint (kind + target + exact change).
  If that fingerprint was ever proposed, approved or rejected, it is not queued again.
  Approved changes disappear naturally because the CRM now agrees with the site.
- **At write time** the app re-fetches the account, confirms it still matches what the
  reviewer saw, re-checks the billing SOP, writes, then re-reads to confirm the change landed.
  Anything that changed in between is marked `conflict`, not overwritten.
- Creates are idempotent: before creating, it checks for an account at that address under Bellhaven.
- Every write appends a dated `[bellhaven-sync]` line to the account note (except the
  SOP-protected old account, which is left exactly as is apart from `chow_current_account`).

## What I'd build next

- Alerting: post a daily summary of new proposals to Slack.
- Auto-approve rules for very low-risk, high-confidence fixes (pure punctuation renames), with an audit log.
- Use the contacts API (administrator names) as another matching signal.
- Run against every parent company we track, not just Bellhaven, from one config list.
