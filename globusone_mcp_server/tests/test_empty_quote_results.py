"""An empty result about one quote must not be read as a fact about that quote.

Live, on Order Process: "What's the order process status for quote Q-1?" (a real
quote id in the log). `get_order_summary` returned `[]`, and the model answered
"All orders for quote Q-1 are Shipped" from rows fetched on an earlier page.
The portal's header said "Order Process In-Progress", with one set still in
Set Allocation. On Set Allocation the same bare `[]` had become "set
allocation has not been confirmed".

An empty per-quote result is now checked against the quote header and returned
as either "not visible to you" or "exists, here is its status, and this
endpoint says nothing more".

Run:  PYTHONPATH=. pytest tests/test_empty_quote_results.py -q
"""

import asyncio

from globus_mcp_server.empty_results import explain_empty, header_status

HEADER = [{"strQuoteId": "Q-1", "strQuoteStatusId": "110830",
           "strQuoteStatusNm": "Order Process In-Progress", "strQuoteTotal": "40518.25"}]


def _explain(tool, result, header=HEADER, quote_id="Q-1"):
    calls = []

    async def fetch(q):
        calls.append(q)
        if isinstance(header, Exception):
            raise header
        return header

    out = asyncio.run(explain_empty(tool, quote_id, result, fetch))
    return out, calls


# ── the live question ─────────────────────────────────────────────────────

def test_an_empty_order_summary_carries_the_quotes_real_status() -> None:
    out, _ = _explain("get_order_summary", [])

    assert out["quoteStatus"] == "Order Process In-Progress"
    assert "NOT evidence" in out["caveat"]
    assert "not shipped" in out["caveat"]


def test_it_forbids_answering_from_an_earlier_turn() -> None:
    """The live answer's rows were not from this turn at all."""
    out, _ = _explain("get_order_summary", [])

    assert "THIS turn" in out["caveat"]


def test_an_empty_allocation_is_not_reported_as_not_confirmed() -> None:
    """The Set Allocation answer from the run before — same class."""
    out, _ = _explain("get_set_allocation_confirmation", [])

    assert "not confirmed" in out["caveat"], "names the wrong conclusion to avoid"
    assert out["quoteStatus"] == "Order Process In-Progress"


# ── a quote that is not there ─────────────────────────────────────────────

def test_an_unknown_quote_is_said_to_be_not_visible() -> None:
    out, _ = _explain("get_order_summary", [], header=[])

    assert out["found"] is False
    assert "No quote Q-1 is visible to you" in out["caveat"]


def test_an_empty_header_needs_no_second_call() -> None:
    """The header IS the existence check."""
    out, calls = _explain("get_quote_header", [])

    assert out["found"] is False
    assert calls == []


# ── what must not change ──────────────────────────────────────────────────

def test_a_result_with_data_passes_through_untouched_and_costs_nothing() -> None:
    rows = [{"strOrderId": "O-1"}]
    out, calls = _explain("get_order_summary", rows)

    assert out is rows
    assert calls == []


def test_a_call_without_a_quote_id_is_left_alone() -> None:
    out, calls = _explain("list_in_transit_return_orders", [], quote_id="")

    assert out == []
    assert calls == []


def test_a_header_outage_still_explains_rather_than_failing() -> None:
    """Unverifiable is not the same as not found."""
    out, _ = _explain("get_order_summary", [], header=RuntimeError("down"))

    assert "found" not in out
    assert "rather than guessing" in out["caveat"]


# ── reading the status off the header ─────────────────────────────────────

def test_the_status_name_is_preferred_over_its_id() -> None:
    assert header_status(HEADER) == "Order Process In-Progress"


def test_a_bare_status_id_is_never_reported_as_the_status() -> None:
    assert header_status({"strQuoteStatusId": "110830"}) == ""


def test_a_header_without_a_status_says_so_rather_than_guessing() -> None:
    out, _ = _explain("get_order_summary", [], header=[{"strQuoteId": "Q-1"}])

    assert "quoteStatus" not in out
    assert "rather than guessing" in out["caveat"]


# ── the real registration applies it ──────────────────────────────────────

def test_every_registered_per_quote_tool_is_wrapped() -> None:
    """A helper nothing calls is inert. Drive the tool FastMCP actually registered."""
    from globus_mcp_server import server

    class Client:
        async def post(self, path, body, auth=None):
            return HEADER if path == "loadQuoteHeaderDetails" else []

    class Auth:
        async def prepare(self, call_ctx=None):
            from globus_mcp_server.auth.base import AuthContext, Identity
            return AuthContext(headers={}, cookies={}, identity=Identity())

    originals = (server._rt.client, server._rt.auth)
    server._rt.client, server._rt.auth = Client(), Auth()
    try:
        tool = server.mcp._tool_manager.get_tool("get_order_summary")
        out = asyncio.run(tool.fn(quote_id="Q-1"))
    finally:
        server._rt.client, server._rt.auth = originals

    assert out["quoteStatus"] == "Order Process In-Progress"


# ── the backend's marker ──────────────────────────────────────────────────

def test_both_empty_verdicts_carry_the_marker_the_backend_reads() -> None:
    """The backend writes the answer itself for a turn of these — the model
    ignored the caveat and repeated an earlier answer. No marker, no fix."""
    exists, _ = _explain("get_order_summary", [])
    missing, _ = _explain("get_order_summary", [], header=[])

    assert exists["nothingReturned"] is True
    assert missing["nothingReturned"] is True


def test_a_result_with_data_carries_no_marker() -> None:
    out, _ = _explain("get_order_summary", [{"strOrderId": "O-1"}])

    assert out == [{"strOrderId": "O-1"}]
