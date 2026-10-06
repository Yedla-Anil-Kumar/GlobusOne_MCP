# GlobusOne MCP Server

A read-only [MCP](https://modelcontextprotocol.io) server that gives the Corporate GPT
assistant access to the GlobusOne portal (Robotic Quote and CRM). It serves the
GlobusOne widget: every tool call runs as the signed-in portal user, with that
user's own permissions, and only the tools for the page the user is on are offered.

- **94 read tools** across opportunities, tasks, quotes, orders, set allocation,
  release for sale, shipping, in-transit returns, metrics and reference data.
  Nothing writes to GlobusOne.
- **Per-user access:** the user's Okta token is verified here and forwarded to the
  GlobusOne API, so each user sees exactly what the portal shows them.
- **Page scoping:** the server decides which tools apply to each portal page.

```
Portal page ─► widget ─► Corporate GPT backend (globus agent) ─► this server ─► GlobusOne API
                         sends the user's token + page URL       /mcp, streamable HTTP
```

## Requirements

- Python 3.12 (what the VM runs)
- `pip install -r requirements.txt` (`mcp`, `httpx`, `PyJWT[crypto]`, `loguru`)
- `pytest` to run the tests

## Run

**Offline, against the captured fixtures** (no portal access needed):

```bash
PYTHONPATH=. python -m globus_mcp_server.server
```

**Live, against the GlobusOne API**, verifying each user's Okta token:

```bash
GLOBUS_DATA_MODE=live \
GLOBUS_AUTH_MODE=passthrough \
GLOBUS_API_BASE=https://erptest3.globusone.com/quote/api/ \
GLOBUS_OKTA_ISSUER=https://globusmedical.oktapreview.com/oauth2/default \
GLOBUS_MCP_PORT=8813 \
PYTHONPATH=. python -m globus_mcp_server.server
```

The MCP endpoint is `http://<host>:<port>/mcp`. Point the backend's `GLOBUS_MCP_URL`
at it.

**Under pm2**, as deployed on the VM: `ecosystem.config.js` holds the live settings
above.

```bash
pm2 start ecosystem.config.js
pm2 logs widget-cgpt-mcp
```

## Configuration

All settings come from environment variables. Never commit a `.env` file.

| Variable | Default | Purpose |
| --- | --- | --- |
| `GLOBUS_DATA_MODE` | `fixtures` | `live` calls the GlobusOne API; `fixtures` answers from `fixtures/` |
| `GLOBUS_API_BASE` | `https://erptest3.globusone.com/quote/api` | GlobusOne API base URL |
| `GLOBUS_AUTH_MODE` | `service` | `passthrough` (per-user, production) or `service` (development only) |
| `GLOBUS_OKTA_ISSUER` | — | Okta issuer; tokens are verified against `<issuer>/v1/keys` (RS256) |
| `GLOBUS_TOKEN_AUDIENCE` | `corporate-gpt-widget` | Expected token audience |
| `GLOBUS_PASSTHROUGH_SECRET` | — | HS256 shared secret, used only when no Okta issuer is set (development) |
| `GLOBUS_SERVICE_USER` / `GLOBUS_SERVICE_SECRET` | — | Service-account credentials (`service` mode) |
| `GLOBUS_SESSION_COOKIE`, `GLOBUS_USER_ID`, `GLOBUS_PARTY_ID`, `GLOBUS_ACCESS_LEVEL`, `GLOBUS_DEPT_NAME`, `GLOBUS_ADMIN_FL` | — | Demo shortcut for `service` mode: a borrowed portal session and its identity. Never in production |
| `GLOBUS_MCP_HOST` / `GLOBUS_MCP_PORT` | `0.0.0.0` / `8812` | Listen address (the VM uses 8813) |
| `GLOBUS_CONNECT_TIMEOUT` / `GLOBUS_READ_TIMEOUT` | `10` / `120` | GlobusOne HTTP timeouts, in seconds |
| `GLOBUS_SWEEP_PAGE_SIZE` / `GLOBUS_SWEEP_MAX_PAGES` / `GLOBUS_SWEEP_MAX_ROWS` | `200` / `10` / `2000` | Limits for tools that page through the whole pipeline |

## Authentication (passthrough mode)

1. The backend forwards the user's Okta token as `Authorization: Bearer <token>`.
2. The token is verified against Okta's JWKS (signature, expiry, audience, issuer).
   A missing or invalid token gives an empty identity, and GlobusOne returns no
   data. It fails closed and never falls back to another account.
3. The user's identity comes from the verified token's subject, never from anything
   the caller sends:
   - `loadUserInfo/<login>` gives the party, access level, company and department;
   - `getAccessPermissions` (`INR_QUOTE_DASH_ADMIN`, `INR_QUOTE_ADMIN`) gives the
     admin flags, which decide whether the territory filter applies, exactly as
     the portal does.

   Both are cached per user for 15 minutes.
4. Every GlobusOne request carries the user's token and identity. The token is
   never logged.

## Page scoping

The backend calls `get_tools_for_page(page_uri)` with the portal URL, and the server
returns the tools for that page. Pages are matched on the `#/` route
(`/quote/#/opportunities` → `opportunities`). The mapping lives in `scoping.py` and
follows which endpoints each portal screen calls. Context tools are always included,
and an unknown page gets only those.

| Page | Route | Tools |
| --- | --- | --- |
| Opportunities | `opportunities` | 34 |
| Tasks | `tasks` | 30 |
| Dashboard | `dashboard` (and `#/`) | 23 |
| Set Allocation | `setallocation` | 23 |
| Order Process | `orderprocess` | 28 |
| Release For Sale | `releaseforsale` | 18 |
| Track Shipments | `shippingdetail` | 20 |
| InTransit Returns | `intransitreturns` | 19 |

Create Quote, Edit Template, Metrics and AR Process have mappings too.
`get_tool_page_mapping` returns the full mapping.

## Tools

| Module | Covers |
| --- | --- |
| `server.py` (registered there) | User context, companies, code lookups, opportunity list, detail and sub-lists, filter options, page scoping |
| `tools/crm.py` | Tasks list, stats, filter options, detail and history; global opportunity/contact search |
| `tools/opportunities.py` | Opportunity search with filters, sorting and value checks |
| `tools/quotes.py` | Quote dashboard, header, line items, discounts, robots |
| `tools/fulfillment.py` | Release for sale, order detail lines and summary, PO and invoices, shipping, in-transit returns |
| `tools/allocation.py` | Set allocation, its confirmation, set details, forex |
| `tools/reference.py`, `governance.py`, `metrics.py`, `templates.py`, `analysis.py` | Plants, codes, divisions, zones, sales reps, rules, quote metrics, templates, pipeline analysis |

Behaviour worth knowing when changing a tool:

- **Request bodies mirror the portal's own screens.** For example, Release For Sale
  loads by robot and country, and Order Process loads its Capital and Metal grids
  separately.
- **Names are resolved to the ids GlobusOne expects, using the same lists the
  screens use:**
  - RSM, zone director and area director names;
  - quote statuses, including a near miss like "Quote Stage";
  - Release For Sale robot and country;
  - company names.

  Unknown or ambiguous values come back with the valid ones, and nothing is queried.
- **List results come back as one object, `{"total": n, "results": [...]}`.**
  Otherwise MCP would split a list into one message per row.
- **Empty results explain themselves:** a quote-scoped tool that finds nothing says
  whether the quote exists and what status it is in, so an empty result is never
  read as "none".
- **Tasks and opportunities send no company filter by default**, matching the
  portal grids. A `company` argument narrows them.
- **Dates are `MM/DD/YYYY`.** ISO dates are converted.

## Tests

```bash
PYTHONPATH=. python -m pytest tests -q
```

The tests run offline, against fake clients and the captured fixtures. Fixtures
return their captured payload whatever the arguments, so filtering is tested with
fake clients that filter the way the GlobusOne APIs do. The identities in the
fixtures and tests are made up.

## Live checks (run on the VM)

| Script | What it checks |
| --- | --- |
| `verify_token.py` | End-to-end per-user auth with one real token. It reads `GLOBUS_END_USER_TOKEN`, enter it with `read -rs`, and never prints it |
| `verify_live.py` | Calls a representative set of tools against the running server and prints pass/fail |
| `verify_rsm_filter.py` | Opportunity filtering by RSM name against the live API |
| `check_portal_drift.py` | Compares what each tool sends with what the portal's screens send, from a browser scan file (`globus_endpoints_<page>.json`). Exits 1 on any difference |

## Layout

```
globus_mcp_server/
  server.py         FastMCP app: tool registration, list wrapping, entry point
  config.py         settings from environment variables
  client.py         GlobusOne HTTP client (live or fixtures) and response unwrapping
  auth/             passthrough (Okta) and service auth, user identity resolution
  companies.py      company scope and company-name resolution
  scoping.py        page → tools mapping
  empty_results.py  explaining empty quote lookups
  codes.py          code id → label cache
  tools/            tool implementations, one module per area
fixtures/           captured GlobusOne responses for offline runs
tests/              offline tests
```
