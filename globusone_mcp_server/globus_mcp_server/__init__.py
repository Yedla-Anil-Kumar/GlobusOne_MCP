"""Globus One MCP server.

Exposes read-only Globus One CRM operations as MCP tools over streamable-HTTP,
so the Corporate GPT assistant can answer questions over live portal data.

Layout:
- ``config.py``   — env-driven settings (data mode, auth mode, transport)
- ``client.py``   — Globus One HTTP client: fixtures or live, envelope unwrap
- ``codes.py``    — coded-value → label resolution cache
- ``auth/``       — pluggable auth (service account / passthrough)
- ``scoping.py``  — page(URI) → tool mapping (server-owned, NEU-978)
- ``tools/``      — the tool implementations
- ``server.py``   — FastMCP app + tool registration + entrypoint
"""

__version__ = "0.1.0"
