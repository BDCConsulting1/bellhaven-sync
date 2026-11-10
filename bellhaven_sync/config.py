"""Settings. The token comes from the environment so it never lands in git."""
from __future__ import annotations

import os
from pathlib import Path

# Load a local .env file if present (simple KEY=VALUE lines).
_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

BASE_URL = os.environ.get("BELLHAVEN_BASE_URL", "https://analyst-assessment-production.up.railway.app").rstrip("/")
API_BASE = BASE_URL + "/api/v1"
TOKEN = os.environ.get("BELLHAVEN_TOKEN", "")

# Optional override if parent auto-detection ever picks the wrong account.
BELLHAVEN_PARENT_ID = os.environ.get("BELLHAVEN_PARENT_ID", "")
PARENT_NAME = "Bellhaven Senior Living"

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("BELLHAVEN_DB", str(DATA_DIR / "sync.db")))

# Website care offering -> CRM care type (used only when creating accounts).
CARE_MAP = {
    "short-term rehabilitation & nursing": "Skilled Nursing",
    "short-term rehabilitation and nursing": "Skilled Nursing",
    "skilled nursing": "Skilled Nursing",
    "assisted living": "Assisted Living",
    "memory support": "Memory Care",
    "memory care": "Memory Care",
    "independent living": "Independent Living",
}


def crm_browser_url(account_id: str) -> str:
    return f"{BASE_URL}/crm/{TOKEN}/accounts/{account_id}"
