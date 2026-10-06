# Globus One MCP Server

Read-only MCP server exposing Globus One CRM operations to the Corporate GPT
assistant over streamable-HTTP. POC scope: opportunities + context tools, plus
page→tool scoping (NEU-978). Builds against HAR-derived fixtures now; swaps to
live HTTP when Globus API access lands.

## Run

```bash
# from the corporate_gpt repo root, using the backend venv (has mcp + httpx)
GLOBUS_MCP_PORT=8812 PYTHONPATH=globus_mcp_server \
  corporate_gpt_gadk/.venv/bin/python -m globus_mcp_server.server
```

Serves the MCP endpoint at `http://<host>:8812/mcp`. Point the Corporate GPT
client (`GLOBUS_MCP_URL`) at it.

## Test (offline, against fixtures)

```bash
PYTHONPATH=globus_mcp_server corporate_gpt_gadk/.venv/bin/python \
  globus_mcp_server/tests/test_smoke.py
```

## Configuration (env)

| Var | Default | Purpose |
|-----|---------|---------|
| `GLOBUS_DATA_MODE` | `fixtures` | `fixtures` (offline) or `live` (real HTTP) |
| `GLOBUS_API_BASE` | `https://erptest3.globusone.com/quote/api` | Globus One API base |
| `GLOBUS_AUTH_MODE` | `service` | `service` (Option B) or `passthrough` (Option A) — NEU-976 pending |
| `GLOBUS_SERVICE_USER` / `_SECRET` | — | Option B credentials |
| `GLOBUS_PASSTHROUGH_SECRET` | — | Option A shared signing secret |
| `GLOBUS_MCP_HOST` / `GLOBUS_MCP_PORT` | `0.0.0.0` / `8812` | Transport bind |
| `GLOBUS_CONNECT_TIMEOUT` / `GLOBUS_READ_TIMEOUT` | `10` / `120` | HTTP timeouts (live) |

## Layout

```
globus_mcp_server/
  config.py     env-driven settings
  client.py     Globus One client: fixtures | live, envelope unwrap
  codes.py      coded-value -> label resolution cache
  auth/         pluggable auth (service / passthrough) — NEU-976 seam
  scoping.py    page(URI) -> tool mapping (server-owned) — NEU-978
  tools/        context + opportunity tool implementations
  server.py     FastMCP app + tool registration + entrypoint
fixtures/       real payloads captured from a live portal session (HAR)
tests/          offline smoke tests
```

## Tools

**Context:** `get_user_context`, `list_companies`, `lookup_codes`
**Opportunities:** `list_opportunities`, `get_opportunity`, `get_opportunity_stats`,
`get_filter_options`, `get_opportunity_products`, `get_opportunity_quotes`,
`get_opportunity_activities`, `get_opportunity_contacts`, `get_opportunity_notes`,
`get_opportunity_history`
**Scoping (NEU-978):** `get_tools_for_page`, `get_tool_page_mapping`

## Notes / next

- **Fixtures don't filter.** In `fixtures` mode every call returns its captured
  payload regardless of args (filters are passed through but not applied). Real
  filtering happens in `live` mode. Tests assert shape/known values, not filtering.
- **Auth (NEU-976)** is stubbed behind `auth/`. `PassthroughAuth._verify` and
  `ServiceAccountAuth._ensure_session` need the real token/login flow once the
  approach is chosen.
- **Quotes/orders/shipments** tools (`list_quotes`, `get_release_for_sale`,
  `track_shipment`, `get_order_status`) are designed but not yet registered —
  Track Shipments / Order Process endpoints still need capture.
