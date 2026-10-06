"""Smoke tests against the HAR-derived fixtures (no network).

Run:  .venv/bin/python -m pytest globus_mcp_server/tests -q
Or standalone: .venv/bin/python globus_mcp_server/tests/test_smoke.py
"""

from __future__ import annotations

import asyncio

from globus_mcp_server.auth import build_auth_strategy
from globus_mcp_server.client import GlobusClient
from globus_mcp_server.codes import CodeResolver
from globus_mcp_server import scoping
from globus_mcp_server.tools import Runtime
from globus_mcp_server.tools import context as ctx
from globus_mcp_server.tools import opportunities as opp


def _rt() -> Runtime:
    c = GlobusClient()
    return Runtime(client=c, codes=CodeResolver(c), auth=build_auth_strategy())


def test_user_context():
    u = asyncio.run(ctx.get_user_context(_rt(), "jdoe"))
    assert u["userId"] == "1000001"
    assert u["partyId"] == "2000002"


def test_lookup_codes_and_resolver():
    rt = _rt()
    rows = asyncio.run(ctx.lookup_codes(rt, "CRMSTG"))
    assert any(r["codeNm"] == "Prepare" for r in rows)

    async def _resolve():
        auth = await rt.auth.prepare()
        await rt.codes.ensure_defaults(auth=auth)
        return rt.codes.resolve(262420122)
    assert asyncio.run(_resolve()) == "Prepare"


def test_list_opportunities():
    res = asyncio.run(opp.list_opportunities(_rt()))
    assert res["total"] == 239
    first = res["opportunities"][0]
    assert first["name"] == "UW Montlake"
    assert first["dealConfidence"] == "Blue"


def test_get_opportunity_detail():
    d = asyncio.run(opp.get_opportunity(_rt(), 272))
    assert d["OPPORTUNITYNAME"] == "UW Montlake"
    assert d["STATUS"] == "Won"


def test_filter_options():
    f = asyncio.run(opp.get_filter_options(_rt()))
    assert "Zeke Schneider" in f["rsms"]


def test_scoping():
    t = scoping.tools_for_page("https://erptest3.globusone.com/quote/#/opportunities")
    assert "list_opportunities" in t
    assert "get_user_context" in t  # context tools always included
    assert "track_shipment" not in t  # not an opportunities-page tool


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all smoke tests passed")
