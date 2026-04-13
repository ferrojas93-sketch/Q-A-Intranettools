"""Runtime configuration and paths for the qa_intranet package."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ.get(
    "QA_INTRANET_BASE_URL",
    "https://intranettools.esic.edu/informes/encuestas",
)
MODEL = os.environ.get("QA_INTRANET_MODEL", "claude-sonnet-4-6")

# Project-local artifacts (kept out of git)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CACHE_DB_PATH = PROJECT_ROOT / "cache.db"
DOWNLOADS_DIR = PROJECT_ROOT / "downloads"

# Per-user session secrets
USER_DIR = Path.home() / ".qa_intranet"
STORAGE_STATE_PATH = USER_DIR / "storage_state.enc"
SALT_PATH = USER_DIR / "salt.bin"


def ensure_dirs() -> None:
    USER_DIR.mkdir(parents=True, exist_ok=True)
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
