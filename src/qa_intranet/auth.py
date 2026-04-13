"""Browser session management.

Two modes are supported, chosen by the ``QA_INTRANET_CDP_URL`` env var:

1. **CDP attach** (recommended for corporate portals): the user runs their
   own Chrome with ``--remote-debugging-port=9222``; we attach to it via
   the DevTools Protocol, borrowing the existing profile and all the
   corporate compliance state (Intune extensions, certificates, OAuth
   tokens, etc.). Nothing is launched by Playwright.

2. **Persistent profile**: launch a Chromium (or Edge/Chrome channel) with
   a user-data-dir at ``~/.qa_intranet/profile`` and do our own SSO the
   first time. Simple but fails when Conditional Access only admits
   managed browsers.

The ``get_context()`` helper hides the difference and yields a Playwright
context plus a ``new_page()`` convenience.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from qa_intranet.config import (
    BASE_URL,
    BROWSER_CHANNEL,
    CDP_URL,
    USER_DIR,
    ensure_dirs,
)

PROFILE_DIR = USER_DIR / "profile"
LOGIN_HOST_FRAGMENTS = ("login.microsoftonline.com", "login.live.com", "adfs")


def _ensure_profile() -> Path:
    ensure_dirs()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return PROFILE_DIR


@contextmanager
def get_context(headless: bool = True, **launch_kwargs) -> Iterator[tuple]:
    """Yield (playwright, context). Uses CDP if QA_INTRANET_CDP_URL is set,
    otherwise launches a persistent context."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        if CDP_URL:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            # Reuse the user's real browser context so cookies/localStorage
            # are the ones the portal already accepted.
            context = (
                browser.contexts[0] if browser.contexts else browser.new_context()
            )
            try:
                yield p, context
            finally:
                # Just detach; don't close the user's browser.
                browser.close()
        else:
            _ensure_profile()
            kwargs = {"headless": headless, **launch_kwargs}
            if BROWSER_CHANNEL:
                kwargs["channel"] = BROWSER_CHANNEL
            context = p.chromium.launch_persistent_context(
                str(PROFILE_DIR), **kwargs
            )
            try:
                yield p, context
            finally:
                context.close()


# Backwards-compat alias used by some callers.
persistent_context = get_context


def has_profile() -> bool:
    """True if we have some form of session available."""
    if CDP_URL:
        # Can't really tell without connecting; assume yes.
        return True
    return PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir())


def verify_connection() -> str:
    """Open the base URL and return the final URL it settled on.

    Raises on error. Used by the `login` command in CDP mode to confirm the
    user's Chrome is reachable and has an active session.
    """
    with get_context(headless=True) as (_, context):
        page = context.new_page()
        try:
            page.goto(BASE_URL, wait_until="load", timeout=60_000)
            page.wait_for_timeout(2000)
            final = page.url
            if any(frag in final for frag in LOGIN_HOST_FRAGMENTS):
                raise RuntimeError(
                    f"The attached browser is not logged in ({final}). "
                    "Log into intranettools in that Chrome window first."
                )
            return final
        finally:
            page.close()


def interactive_login() -> None:
    """Entry point for the `login` command.

    - In CDP mode: just verify we can reach the portal via the user's Chrome.
    - In persistent mode: open a visible browser and wait for the user.
    """
    if CDP_URL:
        print(f"Verifying CDP connection to {CDP_URL}…")
        final = verify_connection()
        print(f"OK. Currently on: {final}")
        return

    print(f"Opening {BASE_URL} in a visible browser window…")
    print("→ Complete the SSO login (and MFA) there.")
    print("→ When you see the intranet dashboard, come back here and press ENTER.")
    print("   (Press Ctrl+C to cancel.)")

    with get_context(headless=False) as (_, context):
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
