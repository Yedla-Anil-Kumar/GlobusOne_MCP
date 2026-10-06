"""End-of-build live verification for the GlobusOne MCP tools.

Runs a representative set of read tools against the RUNNING MCP server (live
mode) and prints a pass/fail matrix. Discovers a real quote id from list_quotes
and chains it through the quote/order/shipping detail tools.

Usage (on g6e, MCP server running live; the port follows GLOBUS_MCP_PORT):
    PYTHONPATH=~/globus_mcp_server python ~/globus_mcp_server/verify_live.py
"""

import os
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

# Read from the server's own settings rather than restated here. This used to be
# hardcoded to 8812 while the server runs on 8813, so every verification run began
# by editing this line — and a stale edit made the script look like an outage.
# Importing `settings` means the two cannot disagree, whatever GLOBUS_MCP_PORT says.
from globus_mcp_server.config import settings  # noqa: E402

URL = f"http://127.0.0.1:{settings.port}/mcp"


def _summarize(text: str):
    try:
        data = json.loads(text)
    except Exception:
        return f"text[{len(text)}]"
    if isinstance(data, list):
        return f"list({len(data)})"
    if isinstance(data, dict):
        if "total" in data:
            return f"total={data['total']}"
        return f"dict({len(data)} keys)"
    return type(data).__name__


def _is_failure(result, text: str) -> bool:
    """Whether a tool call failed, despite having returned a response.

    MCP delivers tool errors as ordinary content text rather than raising, so
    "did it respond?" is not the same question as "did it work?". Treating the
    two as one reported `8 ok, 0 error(s)` for a run in which every single call
    came back `Error executing tool ...: Globus One request failed` — a false
    green during a live deployment check, which is the worst time for one.

    `isError` is the protocol's own flag; the text checks catch servers that
    return a failure as a plain result, which this one does.
    """
    if getattr(result, "isError", False):
        return True
    head = text.lstrip()[:200].lower()
    return head.startswith("error executing tool") or "request failed" in head


async def call(s, name, args):
    try:
        r = await s.call_tool(name, args)
        text = r.content[0].text if r.content else ""
        if _is_failure(r, text):
            return "ERR ", "-", text[:90].replace("\n", " ")
        return "OK  ", _summarize(text), text[:70].replace("\n", " ")
    except Exception as e:  # noqa: BLE001
        return "ERR ", "-", str(e)[:90].replace("\n", " ")


async def main():
    # In passthrough mode the server derives the acting user from this header —
    # it is what Corporate GPT forwards. Without it the server has no identity and
    # every tool fails closed, which looks identical to an outage. Absent in
    # service mode, where the server authenticates with its own session.
    token = (os.environ.get("GLOBUS_END_USER_TOKEN") or "").strip()
    # Tolerate a pasted "Bearer eyJ…" — copying the whole header value is the
    # obvious slip, and it would otherwise send "Bearer Bearer eyJ…" and 401 with
    # no hint as to why.
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    print(f"end-user token: {'sent' if token else 'not set (service mode)'}\n")

    async with streamablehttp_client(URL, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print(f"server advertises {len(tools.tools)} tools\n")

            # Discover a real quote id to chain the detail tools.
            qid = ""
            try:
                res = await s.call_tool("list_quotes", {})
                d = json.loads(res.content[0].text)
                quotes = d.get("quotes") or []
                qid = str(quotes[0]["quoteId"]) if quotes else ""
            except Exception:
                pass
            print(f"discovered quote_id = {qid or '(none)'}\n")

            plan = [
                ("list_opportunities", {"deal_confidence": "Orange"}),
                ("list_quotes", {}),
                ("list_robot_types", {}),
                ("list_all_divisions", {}),
                ("get_ad_vp_list", {}),
                ("get_quotes_by_rep", {"start_date": "01/01/2026", "end_date": "12/31/2026"}),
                ("get_quote_metrics_report", {"start_date": "01/01/2026", "end_date": "12/31/2026"}),
                ("lookup_codes_with_alt_names", {"code_grp": "CRMSTG"}),
            ]
            if qid:
                plan += [
                    ("get_quote_header", {"quote_id": qid}),
                    ("get_quote_line_items", {"quote_id": qid}),
                    ("get_order_summary", {"quote_id": qid}),
                    ("get_po_details", {"quote_id": qid}),
                    ("get_shipping_history", {"quote_id": qid}),
                    ("get_release_for_sale", {"quote_id": qid}),
                ]

            ok = err = 0
            for name, args in plan:
                status, summary, sample = await call(s, name, args)
                if status.strip() == "OK":
                    ok += 1
                else:
                    err += 1
                print(f"{status} {name:38} {summary:12} {sample}")
            print(f"\n{ok} ok, {err} error(s) out of {len(plan)} probed")


def _explain_connect_failure() -> None:
    """Turn "cannot reach the server" into one actionable line.

    The MCP client raises this inside a TaskGroup, so the default output is sixty
    lines of anyio/httpx frames with the actual cause — a port mismatch — nowhere
    in sight. It has cost two debugging rounds already: the server is normally
    started with GLOBUS_MCP_PORT=8813 inline, while this script reads the same
    setting from its own shell, where it defaults to 8812.
    """
    print(f"\nCould not reach the MCP server at {URL}\n")
    print(f"  GLOBUS_MCP_PORT is {os.environ.get('GLOBUS_MCP_PORT') or 'unset'}"
          f" → this script tried port {settings.port}.")
    print("  Check where the server is actually listening:")
    print("      ss -tlnp | grep -E '881[0-9]'")
    print("  Then re-run with the matching port, e.g.:")
    print("      export GLOBUS_MCP_PORT=8813")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BaseException as exc:  # noqa: BLE001 — ExceptionGroup, not just Exception
        if "ConnectError" in repr(exc) or "All connection attempts failed" in repr(exc):
            _explain_connect_failure()
            raise SystemExit(2)
        raise
