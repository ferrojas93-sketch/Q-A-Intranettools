"""CLI entry point: dispatches `login`, `refresh`, or launches the chat REPL."""
from __future__ import annotations

import asyncio
import sys
from typing import Optional

from rich.console import Console
from rich.table import Table

console = Console()


def _cmd_login() -> int:
    from qa_intranet.auth import interactive_login

    try:
        interactive_login()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Login failed:[/red] {exc}")
        return 1
    return 0


def _cmd_refresh(slug: Optional[str] = None) -> int:
    from qa_intranet.refresh import NotAuthenticatedError, refresh_all

    try:
        summary = refresh_all(only_slug=slug)
    except NotAuthenticatedError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        return 2
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Refresh failed:[/red] {exc}")
        return 1

    console.print(
        f"[green]Discovered:[/green] {summary['discovered']} · "
        f"[green]Fetched:[/green] {summary['fetched']} · "
        f"[red]Failed:[/red] {summary['failed']}"
    )
    for err in summary.get("errors", []):
        console.print(f"  [red]-[/red] {err}")
    return 0


def _cmd_list() -> int:
    from qa_intranet.cache import list_tree, session_scope

    with session_scope() as session:
        rows = list_tree(session)

    if not rows:
        console.print(
            "[yellow]No reports in cache yet. Run `refresh` first.[/yellow]"
        )
        return 0

    table = Table(title="Cached reports")
    table.add_column("Slug")
    table.add_column("Title")
    table.add_column("Leaf?")
    table.add_column("Last fetched")
    for r in rows:
        table.add_row(
            r["slug"],
            r["title"],
            "✓" if r["is_leaf"] else "",
            r["last_fetched"] or "-",
        )
    console.print(table)
    return 0


async def _chat_loop() -> int:
    from qa_intranet.agent import build_client

    console.print(
        "[bold cyan]Q&A Intranet Tools[/bold cyan] — escribe una pregunta, "
        "`/list`, `/refresh [slug]`, `/login` o `/quit`."
    )

    client = build_client()
    async with client:
        while True:
            try:
                prompt = console.input("[bold green]you>[/bold green] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                return 0
            if not prompt:
                continue

            if prompt.startswith("/"):
                parts = prompt.split()
                cmd = parts[0].lower()
                if cmd == "/quit" or cmd == "/exit":
                    return 0
                if cmd == "/list":
                    _cmd_list()
                    continue
                if cmd == "/login":
                    _cmd_login()
                    continue
                if cmd == "/refresh":
                    slug = parts[1] if len(parts) > 1 else None
                    _cmd_refresh(slug=slug)
                    continue
                console.print(f"[yellow]Unknown command: {cmd}[/yellow]")
                continue

            try:
                await client.query(prompt)
                async for message in client.receive_response():
                    _render_message(message)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]Agent error:[/red] {exc}")


def _render_message(message) -> None:
    """Pretty-print a streaming message from ClaudeSDKClient."""
    # The SDK yields structured messages; render any text blocks we see.
    for attr in ("content", "message"):
        content = getattr(message, attr, None)
        if content is None:
            continue
        if isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None) or (
                    block.get("text") if isinstance(block, dict) else None
                )
                if text:
                    console.print(f"[cyan]claude>[/cyan] {text}")
        elif isinstance(content, str):
            console.print(f"[cyan]claude>[/cyan] {content}")


def _cmd_open_chrome(argv: list[str]) -> int:
    """Launch the user's own Chrome with --remote-debugging-port.

    Uses a dedicated user-data-dir so it doesn't conflict with a Chrome that
    the user may already have open. The user logs in there once (SSO + MFA)
    and then leaves the window open while running other qa_intranet commands
    (they will attach to this Chrome via CDP).
    """
    import argparse
    import shutil
    import subprocess
    from pathlib import Path

    parser = argparse.ArgumentParser(
        prog="qa_intranet open-chrome",
        description=(
            "Launch Chrome with --remote-debugging-port so that qa_intranet "
            "can attach to it via CDP. Keep this Chrome window open while "
            "running login/refresh/capture-network."
        ),
    )
    parser.add_argument("--port", type=int, default=9222, help="CDP port (default 9222)")
    parser.add_argument(
        "--profile-dir",
        default=str(Path.home() / "ChromeQAIntranet"),
        help="User-data-dir for this Chrome instance",
    )
    parser.add_argument(
        "--chrome-path",
        default=None,
        help="Override path to chrome.exe (auto-detected on Windows by default)",
    )
    parser.add_argument(
        "--url",
        default="https://intranettools.esic.edu/informes/encuestas",
        help="URL to open in the first tab",
    )
    opts = parser.parse_args(argv)

    chrome_path = opts.chrome_path
    if not chrome_path:
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            shutil.which("chrome"),
            shutil.which("google-chrome"),
        ]
        chrome_path = next((c for c in candidates if c and Path(c).exists()), None)
    if not chrome_path:
        console.print(
            "[red]Could not find chrome.exe.[/red] Pass --chrome-path explicitly."
        )
        return 1

    profile = Path(opts.profile_dir)
    profile.mkdir(parents=True, exist_ok=True)

    cmd = [
        chrome_path,
        f"--remote-debugging-port={opts.port}",
        f"--user-data-dir={profile}",
        opts.url,
    ]
    console.print(f"[green]Launching:[/green] {' '.join(cmd)}")
    subprocess.Popen(cmd, close_fds=True)
    console.print(
        "[cyan]Chrome launched.[/cyan] Log into ESIC there and keep the "
        "window open.\n"
        f"Set [bold]QA_INTRANET_CDP_URL=http://localhost:{opts.port}[/bold] "
        "in your .env so the other commands attach to this Chrome."
    )
    return 0


def _cmd_capture_network(argv: list[str]) -> int:
    import argparse
    from pathlib import Path

    from qa_intranet.refresh import NotAuthenticatedError, capture_network

    parser = argparse.ArgumentParser(
        prog="qa_intranet capture-network",
        description=(
            "Open a visible browser with the saved session and record every "
            "API-ish request your manual navigation triggers."
        ),
    )
    parser.add_argument(
        "--output",
        default="network.jsonl",
        help="JSONL file where the events are saved (default: network.jsonl)",
    )
    opts = parser.parse_args(argv)

    try:
        summary = capture_network(Path(opts.output))
    except NotAuthenticatedError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        return 2
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Capture failed:[/red] {exc}")
        return 1

    console.print(
        f"[green]Captured[/green] {summary['events']} events to "
        f"[bold]{summary['output_path']}[/bold]"
    )
    console.print(
        f"[cyan]Unique API URLs ({len(summary['unique_api_urls'])}):[/cyan]"
    )
    for url in summary["unique_api_urls"]:
        console.print(f"  {url}")
    return 0


def _cmd_dump_html(argv: list[str]) -> int:
    import argparse

    from qa_intranet.config import BASE_URL
    from qa_intranet.refresh import NotAuthenticatedError, fetch_page_html

    parser = argparse.ArgumentParser(
        prog="qa_intranet dump-html",
        description="Fetch a page with the saved session and save its HTML.",
    )
    parser.add_argument("--url", default=BASE_URL, help="URL to fetch (default: BASE_URL)")
    parser.add_argument(
        "--output",
        default="menu_dump.html",
        help="Output file path (default: menu_dump.html)",
    )
    opts = parser.parse_args(argv)

    try:
        html, final_url = fetch_page_html(opts.url)
    except NotAuthenticatedError as exc:
        console.print(f"[yellow]{exc}[/yellow]")
        return 2
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Dump failed:[/red] {exc}")
        return 1

    from pathlib import Path

    out = Path(opts.output)
    out.write_text(html, encoding="utf-8")
    console.print(
        f"[green]Saved[/green] {len(html):,} chars to [bold]{out.resolve()}[/bold]"
    )
    console.print(f"Final URL: {final_url}")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        return asyncio.run(_chat_loop())

    cmd = args[0]
    if cmd == "login":
        return _cmd_login()
    if cmd == "refresh":
        slug = args[1] if len(args) > 1 else None
        return _cmd_refresh(slug=slug)
    if cmd == "list":
        return _cmd_list()
    if cmd in ("chat", "repl"):
        return asyncio.run(_chat_loop())
    if cmd == "dump-html":
        return _cmd_dump_html(args[1:])
    if cmd == "capture-network":
        return _cmd_capture_network(args[1:])
    if cmd == "open-chrome":
        return _cmd_open_chrome(args[1:])
    if cmd == "parse-har":
        return _cmd_parse_har(args[1:])
    if cmd == "extract-data":
        return _cmd_extract_data(args[1:])

    console.print(f"[red]Unknown command:[/red] {cmd}")
    console.print(
        "Usage: python -m qa_intranet "
        "[login|refresh [slug]|list|chat|"
        "dump-html [--url URL] [--output PATH]|"
        "capture-network [--output PATH]|"
        "open-chrome [--port N] [--chrome-path PATH]|"
        "parse-har <har_path> [--bodies-dir DIR] [--all]|"
        "extract-data [--bodies-dir DIR] [--output JSON]]"
    )
    return 1


def _cmd_extract_data(argv: list[str]) -> int:
    """Parse every QES body saved in a bodies directory and summarise tables."""
    import argparse
    import json
    from pathlib import Path

    from qa_intranet.dsr_parser import summarise_directory

    parser = argparse.ArgumentParser(
        prog="qa_intranet extract-data",
        description=(
            "Walk a bodies directory (produced by parse-har) and decode every "
            "Power BI DSR response into tabular rows. Writes a summary JSON "
            "with column names, row counts and a small sample per table."
        ),
    )
    parser.add_argument(
        "--bodies-dir",
        default="network_bodies",
        help="Directory produced by parse-har (default: network_bodies)",
    )
    parser.add_argument(
        "--output",
        default="extracted_tables.json",
        help="Path to write the summary JSON (default: extracted_tables.json)",
    )
    opts = parser.parse_args(argv)

    bodies = Path(opts.bodies_dir)
    if not bodies.exists():
        console.print(f"[red]Bodies dir not found:[/red] {bodies}")
        return 1

    summary = summarise_directory(bodies)
    Path(opts.output).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    total_tables = sum(1 for s in summary if "columns" in s)
    total_rows = sum(s.get("row_count", 0) for s in summary if "columns" in s)
    errors = [s for s in summary if "error" in s]

    console.print(
        f"[green]Parsed {total_tables} tables[/green] "
        f"([bold]{total_rows:,}[/bold] rows total) from "
        f"{len([s for s in summary if 'file' in s])} files."
    )
    if errors:
        console.print(f"[yellow]{len(errors)} files failed to parse[/yellow]")
        for err in errors[:5]:
            console.print(f"  {err['file']}: {err['error']}")
    console.print(f"[cyan]Summary written to[/cyan] {Path(opts.output).resolve()}")

    # Show top 5 tables by row count
    populated = sorted(
        [s for s in summary if s.get("row_count", 0) > 0],
        key=lambda s: s["row_count"],
        reverse=True,
    )
    if populated:
        console.print("\n[bold]Top tables (by row count):[/bold]")
        for s in populated[:5]:
            console.print(
                f"  [magenta]{s['file']}[/magenta] · "
                f"{s['row_count']} rows · "
                f"cols={', '.join(s['columns'])[:100]}"
            )
    return 0


def _cmd_parse_har(argv: list[str]) -> int:
    """Extract Power BI response bodies from a HAR file exported by Chrome DevTools."""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        prog="qa_intranet parse-har",
        description=(
            "Read a HAR file exported from Chrome DevTools (Network tab, "
            "Save all as HAR with content) and extract Power BI data bodies."
        ),
    )
    parser.add_argument("har_path", help="Path to the .har file")
    parser.add_argument(
        "--bodies-dir",
        default="network_bodies",
        help="Directory to write extracted body files (default: network_bodies)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Extract every response, not just Power BI data endpoints",
    )
    opts = parser.parse_args(argv)

    har = Path(opts.har_path)
    if not har.exists():
        console.print(f"[red]HAR file not found:[/red] {har}")
        return 1

    from qa_intranet.har_parser import extract_to_bodies_dir

    try:
        summary = extract_to_bodies_dir(
            har, Path(opts.bodies_dir), only_data=not opts.all
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]parse-har failed:[/red] {exc}")
        return 1

    console.print(
        f"[green]Parsed {summary['total_entries']} entries[/green], "
        f"[bold]{summary['saved']}[/bold] bodies saved to "
        f"[bold]{summary['bodies_dir']}[/bold]."
    )
    if summary["skipped_without_body"]:
        console.print(
            f"[yellow]{summary['skipped_without_body']} matching entries had "
            "no response body (record again with 'Preserve log' and content).[/yellow]"
        )
    console.print(
        f"[cyan]Unique data URLs ({len(summary['unique_data_urls'])}):[/cyan]"
    )
    for u in summary["unique_data_urls"]:
        console.print(f"  {u}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
