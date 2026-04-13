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

    console.print(f"[red]Unknown command:[/red] {cmd}")
    console.print("Usage: python -m qa_intranet [login|refresh [slug]|list|chat]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
