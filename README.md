# Q&A Intranet Tools (ESIC)

Agente conversacional (CLI) que responde preguntas en lenguaje natural sobre los
informes y subinformes de encuestas publicados en
`https://intranettools.esic.edu/informes/encuestas`.

- **Login**: Azure AD SSO vía Playwright (primera vez interactivo, luego cookies
  cifradas localmente con Fernet).
- **Descubrimiento**: recorre el menú de informes y descarga los exports
  Excel/CSV de cada subinforme.
- **Cache**: SQLite local con FTS5 para búsqueda full-text.
- **Chat**: REPL con Claude Agent SDK (`claude-sonnet-4-6` por defecto) que
  expone herramientas para listar, buscar y leer informes.

## Requisitos

- Python 3.11+
- Chromium (se instala con `playwright install chromium`)
- Una API key de Anthropic (`ANTHROPIC_API_KEY`)

## Instalación

```bash
pip install -e .
playwright install chromium
cp .env.example .env
# edita .env y pon tu ANTHROPIC_API_KEY
```

## Uso

```bash
# 1) Primer login interactivo — abre un Chromium para que completes SSO+MFA.
python -m qa_intranet login

# 2) Descubre e indexa los informes en el cache local.
python -m qa_intranet refresh

# 3) Lanza el chat Q&A.
python -m qa_intranet
```

Dentro del REPL:

- `/list` — muestra el árbol de informes cacheados.
- `/refresh` — refresca todo el cache.
- `/refresh <slug>` — refresca un informe concreto.
- `/login` — re-autentica si la sesión expiró.
- `/quit` — salir.

## Seguridad

- El `storage_state.json` de Playwright se guarda cifrado con Fernet en
  `~/.qa_intranet/storage_state.enc`. La clave deriva de una passphrase local
  (PBKDF2, 100k iteraciones).
- El cache SQLite y las descargas quedan en el directorio del proyecto
  (`cache.db`, `downloads/`) y están excluidos de git.
- Nunca se commitean credenciales, cookies ni datos de informes.

## Estructura

```
src/qa_intranet/
├── __main__.py   # entry point: `python -m qa_intranet`
├── cli.py        # REPL + dispatch de subcomandos (login/refresh/chat)
├── auth.py       # Playwright login + cifrado de storage_state
├── crawler.py    # descubrimiento del menú de informes
├── extractor.py  # descarga de exports Excel/CSV
├── cache.py      # SQLAlchemy models + FTS5
├── tools.py      # herramientas para Claude Agent SDK
├── agent.py      # configuración del agente
└── config.py     # rutas y constantes
```

## Verificación end-to-end

```bash
pytest
python -m qa_intranet login       # abre browser, haz login, se cierra solo
python -m qa_intranet refresh     # logs: "Discovered N reports, M subreports"
python -m qa_intranet             # chat
```
