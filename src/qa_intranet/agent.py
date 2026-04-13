"""Claude Agent SDK session: wires the intranet tools into a ClaudeSDKClient."""
from __future__ import annotations

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    create_sdk_mcp_server,
)

from qa_intranet.config import MODEL
from qa_intranet.tools import ALL_TOOLS

SYSTEM_PROMPT = """Eres un asistente analítico que responde preguntas sobre los
informes y subinformes de encuestas disponibles en la intranet de ESIC.

Tienes estas herramientas:
- list_reports(parent_slug?): recorre el árbol del menú.
- search_reports(query, limit?): búsqueda full-text por título y contenido.
- get_report(slug, offset?, limit?): devuelve filas y metadatos de un informe.
- refresh_report(slug): re-descarga un informe si el usuario lo pide.

Directrices:
1. Empieza por entender qué informe o subconjunto necesita el usuario — usa
   search_reports o list_reports antes de pedir get_report.
2. Cuando cites datos, indica el slug del informe y la fecha de last_fetched.
3. Si un informe tiene más filas de las devueltas, pide más con offset/limit.
4. Si la sesión expira (errores de autenticación), sugiere correr
   `/login` en el CLI. No intentes hacer login por tu cuenta.
5. Responde en el idioma del usuario (por defecto español) y sé conciso."""


def build_client() -> ClaudeSDKClient:
    server = create_sdk_mcp_server(
        name="qa-intranet-tools",
        version="0.1.0",
        tools=ALL_TOOLS,
    )

    allowed = [f"mcp__qa-intranet-tools__{t.name}" for t in ALL_TOOLS]

    options = ClaudeAgentOptions(
        model=MODEL,
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"qa-intranet-tools": server},
        allowed_tools=allowed,
    )
    return ClaudeSDKClient(options=options)
