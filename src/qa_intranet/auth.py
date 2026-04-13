"""Interactive Azure AD login via Playwright with encrypted session reuse.

The first run opens a visible Chromium window so the user can complete
Microsoft SSO (including MFA) manually. Once the final post-login URL is
reached, the storage_state is serialized, encrypted with Fernet and written
to ~/.qa_intranet/storage_state.enc.

Subsequent runs decrypt the state into memory and open a headless context
that's already authenticated.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from getpass import getpass
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from qa_intranet.config import (
    BASE_URL,
    SALT_PATH,
    STORAGE_STATE_PATH,
    ensure_dirs,
)

PBKDF2_ITERATIONS = 100_000
# URL fragments that indicate "still on the login flow" (not authenticated yet).
LOGIN_HOST_FRAGMENTS = ("login.microsoftonline.com", "login.live.com", "adfs")


# --- Encryption helpers ------------------------------------------------------

def _get_or_create_salt() -> bytes:
    ensure_dirs()
    if SALT_PATH.exists():
        return SALT_PATH.read_bytes()
    salt = secrets.token_bytes(16)
    SALT_PATH.write_bytes(salt)
    return salt


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _get_passphrase(prompt: str = "Session passphrase: ") -> str:
    env = os.environ.get("SESSION_PASSPHRASE")
    if env:
        return env
    return getpass(prompt)


def _fernet() -> Fernet:
    salt = _get_or_create_salt()
    key = _derive_key(_get_passphrase(), salt)
    return Fernet(key)


def save_storage_state(state: dict) -> None:
    ensure_dirs()
    token = _fernet().encrypt(json.dumps(state).encode("utf-8"))
    STORAGE_STATE_PATH.write_bytes(token)


def load_storage_state() -> Optional[dict]:
    if not STORAGE_STATE_PATH.exists():
        return None
    try:
        data = _fernet().decrypt(STORAGE_STATE_PATH.read_bytes())
    except InvalidToken as exc:
        raise RuntimeError(
            "Could not decrypt storage_state — wrong passphrase?"
        ) from exc
    return json.loads(data)


def storage_state_to_tempfile(state: dict, tmp_dir: Path) -> Path:
    """Playwright accepts storage_state as a file path; write a short-lived one."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / "storage_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


# --- Login flow --------------------------------------------------------------

def interactive_login(timeout_seconds: int = 600) -> None:
    """Open a visible browser; wait until the user confirms login, then persist state.

    Rather than trying to detect a successful login automatically (fragile with
    various Azure AD tenants and MFA flows), we open the browser, let the user
    complete whatever is needed, and wait for them to press Enter in the
    terminal. We then verify the current URL is on the target host before
    saving the session.
    """
    from playwright.sync_api import sync_playwright

    print(f"Opening {BASE_URL} in a visible browser window…")
    print("→ Complete the SSO login (and MFA) there.")
    print("→ When you can see the intranet dashboard, come back here and press ENTER.")
    print("   (Press Ctrl+C to cancel.)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(BASE_URL, wait_until="domcontentloaded")

        try:
            input("\nPress ENTER once you have logged in... ")
        except (KeyboardInterrupt, EOFError):
            browser.close()
            raise RuntimeError("Login cancelled.") from None

        current_url = page.url
        if any(frag in current_url for frag in LOGIN_HOST_FRAGMENTS):
            browser.close()
            raise RuntimeError(
                f"Still on the login page ({current_url}). "
                "Finish the SSO flow in the browser and try again."
            )
        if not current_url.startswith("https://intranettools.esic.edu"):
            browser.close()
            raise RuntimeError(
                f"Unexpected final URL: {current_url}. "
                "Navigate to the intranettools dashboard before pressing Enter."
            )

        state = context.storage_state()
        browser.close()

    save_storage_state(state)
    print(f"Saved encrypted session to {STORAGE_STATE_PATH}")
