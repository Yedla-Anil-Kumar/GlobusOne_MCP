"""Changes taken from the portal's final codebase catalogue.

  * GmOppDashboardSqlBuilder filters on the opportunity name (partial match) and
    close quarter ("Q1,Q3") too; the tool could not ask for either.
  * loadQuoteDashboardDtls filters strQuoteStatus as an IN list of status IDS,
    so "Approved vs Draft" is one query with two ids, and names match nothing.
  * getDivision / getZones / getRegions answer with a bare JSON array, not the
    usual envelope; unwrap would have raised on it.

Run:  PYTHONPATH=. pytest tests/test_final_catalogue.py -q
"""

import asyncio

from globus_mcp_server.client import GlobusClient
from globus_mcp_server.tools import opportunities, quotes
from globus_mcp_server.tools.opportunities import _FILTER_MAP


def test_the_opportunity_list_filters_on_name_and_quarter() -> None:
    assert _FILTER_MAP["opp_name"] == "strOppName"
    assert _FILTER_MAP["quarters"] == "strQuarters"


def test_name_and_quarter_reach_the_request() -> None:
    bodies = []

    class Client:
        async def post_envelope(self, path, body, auth=None):
            bodies.append(dict(body))
            return {"strStatus": "success", "lstDefault": [{"strOppId": "1"}], "strMessage": "1"}

    class Codes:
        async def ensure_defaults(self, auth=None):
            return None

    class Rt:
        client, codes = Client(), Codes()

        async def context(self, call_ctx=None):
            return {}

    asyncio.run(opportunities.list_opportunities(Rt(), opp_name="Weston", quarters="Q1,Q3"))

    assert bodies[0]["strOppName"] == "Weston"
    assert bodies[0]["strQuarters"] == "Q1,Q3"


# ── Dashboard status lists ────────────────────────────────────────────────

ROWS = [{"strQuoteId": "A", "strQuoteStatusNm": "Approved", "strQuoteStatusId": "110817"},
        {"strQuoteId": "D", "strQuoteStatusNm": "Draft", "strQuoteStatusId": "110814"}]


class DashClient:
    def __init__(self):
        self.bodies = []

    async def post(self, path, body, auth=None):
        self.bodies.append(dict(body))
        wanted = set(filter(None, str(body.get("strQuoteStatus") or "").split(",")))
        if not body:                                  # the unfiltered vocabulary call
            return ROWS
        return [r for r in ROWS if r["strQuoteStatusId"] in wanted] if wanted else ROWS


class DashRt:
    def __init__(self):
        self.client = DashClient()

    async def context(self, call_ctx=None):
        return {}


def test_several_status_names_become_one_query_by_ids() -> None:
    rt = DashRt()
    out = asyncio.run(quotes.list_quotes(rt, status="Approved,Draft"))

    assert out["total"] == 2
    assert rt.client.bodies[-1]["strQuoteStatus"] == "110817,110814"


def test_an_unknown_name_among_known_ones_is_reported_and_the_rest_still_queried() -> None:
    rt = DashRt()
    out = asyncio.run(quotes.list_quotes(rt, status="Approved,Shipped Twice"))

    assert out["total"] == 1
    assert rt.client.bodies[-1]["strQuoteStatus"] == "110817"


# ── bare-array responses ──────────────────────────────────────────────────

def test_a_bare_array_response_is_the_data() -> None:
    assert GlobusClient.unwrap([{"zoneId": "1"}]) == [{"zoneId": "1"}]


def test_a_bare_map_response_is_the_data() -> None:
    assert GlobusClient.unwrap({"robotNameExists": True}) == {"robotNameExists": True}


def test_an_is_empty_failure_is_zero_rows() -> None:
    """fetchALcarteDuraProVerzeraItems answers an empty list with FAIL + "…is empty"."""
    env = {"strStatus": "FAIL", "strMessage": "A la carte list is empty"}

    assert GlobusClient.unwrap(env) == []


# ── the last six response keys ────────────────────────────────────────────

def test_an_uppercase_success_is_success() -> None:
    assert GlobusClient.unwrap({"strStatus": "SUCCESS", "hashMap": {"a": 1}}) == {"a": 1}


def test_hashmap_endpoints_return_the_map_itself() -> None:
    """loadOrderSummary, loadShippingMode, loadForexDropDownValues."""
    env = {"strStatus": "success", "strMessage": "", "hashMap": {"orders": [1, 2]}}

    assert GlobusClient.unwrap(env) == {"orders": [1, 2]}


def test_intresult_endpoints_return_the_number() -> None:
    """validateQuoteId (match count), fetchQuoteCompanyId (company id)."""
    assert GlobusClient.unwrap({"strStatus": "success", "intResult": 1000}) == 1000
    assert GlobusClient.unwrap({"strStatus": "success", "intResult": 0}) == 0


def test_the_forex_flag_is_the_answer_not_a_status_field() -> None:
    """It used to be discarded with strStatus, so validateForexRate came back empty."""
    assert GlobusClient.unwrap({"strStatus": "success", "flag": True}) is True
    assert GlobusClient.unwrap({"strStatus": "success", "flag": False}) is False


def test_flag_does_not_shadow_the_real_data_elsewhere() -> None:
    """Other envelopes carry `flag` alongside their data (see the fixtures)."""
    env = {"strStatus": "success", "flag": True, "objResult": {"oppId": 1}}

    assert GlobusClient.unwrap(env) == {"oppId": 1}


def _validate(count):
    class Client:
        async def post(self, path, body, auth=None):
            return count

    class Rt:
        client = Client()

        async def context(self, call_ctx=None):
            return {}

    return asyncio.run(quotes.validate_quote_id(Rt(), "Q-1"))


def test_a_zero_match_count_is_a_quote_that_is_not_there() -> None:
    out = _validate(0)

    assert out["found"] is False and out["nothingReturned"] is True


def test_a_positive_count_is_a_quote_that_exists() -> None:
    assert _validate(1) == {"quoteId": "Q-1", "found": True, "matches": 1}


# ── one block per result ──────────────────────────────────────────────────

def _call_registered(tool, args, reply):
    from globus_mcp_server import server
    from globus_mcp_server.auth.base import AuthContext, Identity

    class Client:
        async def post(self, path, body, auth=None):
            return reply

        async def get(self, path, auth=None):
            return reply

    class Auth:
        async def prepare(self, call_ctx=None):
            return AuthContext(headers={}, cookies={}, identity=Identity())

    originals = (server._rt.client, server._rt.auth)
    server._rt.client, server._rt.auth = Client(), Auth()
    try:
        res = asyncio.run(server.mcp.call_tool(tool, args))
    finally:
        server._rt.client, server._rt.auth = originals
    return res[0] if isinstance(res, tuple) else res


def test_a_list_result_arrives_as_one_block() -> None:
    """FastMCP splits a list into one block per row; the backend read the first
    as the whole result, and the model got all 1,032 rows of Release For Sale."""
    rows = [{"setid": f"S-{i}"} for i in range(50)]
    blocks = _call_registered("get_release_for_sale", {"quote_id": "Q-1", "country": "1"}, rows)

    assert len(blocks) == 1
    import json
    payload = json.loads(blocks[0].text)
    assert payload["total"] == 50 and len(payload["results"]) == 50


def test_hand_registered_opportunity_lists_arrive_as_one_block_too() -> None:
    blocks = _call_registered("get_opportunity_products", {"opp_id": 1},
                              [{"p": 1}, {"p": 2}])

    assert len(blocks) == 1


def test_the_confirmation_flag_reads_as_confirmed_or_not() -> None:
    from globus_mcp_server.tools import allocation

    class Client:
        async def post(self, path, body, auth=None):
            return "N"

    class Rt:
        client = Client()

        async def context(self, call_ctx=None):
            return {}

    out = asyncio.run(allocation.get_set_allocation_confirmation(Rt(), "Q-1"))

    assert (out["confirmed"], out["flag"]) == (False, "N")
    assert "quote header" in out["note"], "the header is the authority on confirmation"


# ── the header's dates ────────────────────────────────────────────────────

def test_the_header_is_asked_for_with_the_date_format() -> None:
    """Without it, the confirmed date came back empty for a quote the portal shows
    confirmed on 10/29/2025."""
    bodies = []

    class Client:
        async def post(self, path, body, auth=None):
            bodies.append((path, dict(body)))
            return [{"strconfirmedby": "A Person", "strconfirmeddate": "10/29/2025"}]

    class Rt:
        client = Client()

        async def context(self, call_ctx=None):
            return {}

    asyncio.run(quotes.get_quote_header(Rt(), "Q-1"))
    from globus_mcp_server.tools import fulfillment
    asyncio.run(fulfillment.get_order_summary(Rt(), "Q-1"))

    headers = [b for p, b in bodies if p == "loadQuoteHeaderDetails"]
    assert headers and all(b.get("strDtFormat") == "MM/dd/yyyy" for b in headers)


def test_a_non_flag_confirmation_value_points_at_the_header() -> None:
    """Live: 6143.2782 — a part number — came back as the "flag"."""
    from globus_mcp_server.tools import allocation

    class Client:
        async def post(self, path, body, auth=None):
            return 6143.2782

    class Rt:
        client = Client()

        async def context(self, call_ctx=None):
            return {}

    out = asyncio.run(allocation.get_set_allocation_confirmation(Rt(), "Q-1"))

    assert out["value"] == 6143.2782
    assert "get_quote_header" in out["note"]
