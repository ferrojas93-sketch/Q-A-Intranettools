"""Azure AD login via Playwright with a persistent browser profile.

Rather than serializing Playwright's `storage_state` (which can miss
session-only tokens stored in localStorage/sessionStorage by Angular SPAs),
we launch Chromium with a persistent user-data directory in
``~/.qa_intranet/profile``. The browser behaves like a normal install:
cookies, localStorage, IndexedDB, and any OAuth tokens survive across
runs, so authenticated API calls fired by the dashboard work in
subsequent headless invocations.

The directory lives inside the user's home, protected by the OS account;
no encryption is applied on top because it doesn't add value (a user with
read access to the profile can just launch the browser manually).
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from qa_intranet.config import BASE_URL, BROWSER_CHANNEL, USER_DIR, ensure_dirs

PROFILE_DIR = USER_DIR / "profile"

LOGIN_HOST_FRAGMENTS = ("login.microsoftonline.com", "login.live.com", "adfs")


def _ensure_profile() -> Path:
    ensure_dirs()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILE_DIR


@contextmanager
def persistent_context(headless: bool = True, **kwargs) -> Iterator[tuple]:
    """Yield (playwright, context) using the persistent profile directory.

    Respects QA_INTRANET_BROWSER: set to "msedge" or "chrome" to use the
    corresponding browser installed on the system (required when Azure AD
    Conditional Access blocks the bundled Chromium).
    """
    from playwright.sync_api import sync_playwright

    _ensure_profile()
    with sync_playwright() as p:
        launch_kwargs = {"headless": headless, **kwargs}
        if BROWSER_CHANNEL:
            launch_kwargs["channel"] = BROWSER_CHANNEL
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            **launch_kwargs,
        )
        try:
            yield p, context
        finally:
            context.close()


def has_profile() -> bool:
    """True if the persistent profile exists and seems initialised."""
    return PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir())


def interactive_login() -> None:
    """Open a visible browser with the persistent profile; wait for user.

    The user completes SSO + MFA manually. When they press Enter, the
    profile is already saved on disk because Chromium writes it live —
    there is nothing extra to serialise.
    """
    print(f"Opening {BASE_URL} in a visible browser window…")
    print("→ Complete the SSO login (and MFA) there.")
    print("→ When you see the intranet dashboard, come back here and press ENTER.")
    print("   (Press Ctrl+C to cancel.)")

    with persistent_context(headless=False) as (_, context):
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(BASE_URL, wait_until="domcontentloaded")

        try:
            input("\nPress ENTER once you have logged in... ")
        except (KeyboardInterrupt, EOFError):
            raise RuntimeError("Login cancelled.") from None

        current_url = page.url
        if any(frag in current_url for frag in LOGIN_HOST_FRAGMENTS):
            raise RuntimeError(
                f"Still on the login page ({current_url}). "
                "Finish the SSO flow in the browser and try again."
            )
        if not current_url.startswith("https://intranettools.esic.edu"):
            raise RuntimeError(
                f"Unexpected final URL: {current_url}. "
                "Navigate to the intranettools dashboard before pressing Enter."
            )

    print(f"Session saved under {PROFILE_DIR}")
