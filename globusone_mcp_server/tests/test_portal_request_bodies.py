"""Requests carry the fields the portal's own screens send.

Taken from the portal's CURRENT browser code (a scan of the scripts erptest3
serves), not from the old codebase extract. Each of these returned nothing, or
ignored a filter, because a field was missing or misnamed:

  * Order Process — `loadOrderSummary` gets the quote's capital account as well
    as its id, read from the quote header the screen has already loaded. We sent
    the id alone; a quote with orders placed came back empty, and the model
    filled the gap with a wrong "all orders shipped".
  * Dashboard — the date range is `strQuoteFromDate`/`strQuoteToDate`. We sent
    `strQuoteStartDate`/`strQuoteExpDate`, which it ignores: every date filter
    was dropped.
  * Set Allocation — `loadSetAllocationDetails` gets the quote id and the date
    format. We also sent empty set and plant ids the screen never sends.
  * `fetchConfirmationFlag` appears nowhere in the current scripts, though the
    codebase still has the endpoint; it stays offered on Set Allocation.

Run:  PYTHONPATH=. pytest tests/test_portal_request_bodies.py -q
"""

import asyncio

import pytest

from globus_mcp_server.scoping import PAGE_TOOLS
from globus_mcp_server.tools import allocation, fulfillment, quotes


class Client:
    def __init__(self, header=None):
        self.calls = []
        self.header = header if header is not None else [{"strQuoteId": "Q-1", "strCapitalAccId": 40298}]

    async def post(self, path, body, auth=None):
        self.calls.append((path, dict(body)))
        return self.header if path == "loadQuoteHeaderDetails" else []


class Rt:
    def __init__(self, client):
        self.client = client

    async def context(self, call_ctx=None):
        return {}


def _call(fn, client, **kwargs):
    asyncio.run(fn(Rt(client), **kwargs))
    return client.calls


# ── Order Process ─────────────────────────────────────────────────────────

def test_the_order_summary_carries_the_quotes_capital_account() -> None:
    calls = _call(fulfillment.get_order_summary, Client(), quote_id="Q-1")

    assert calls[-1] == ("loadOrderSummary", {"strQuoteId": "Q-1", "strCapitalAccId": "40298"})


def test_the_account_is_read_from_the_header_first() -> None:
    calls = _call(fulfillment.get_order_summary, Client(), quote_id="Q-1")

    assert [p for p, _ in calls] == ["loadQuoteHeaderDetails", "loadOrderSummary"]


def test_a_header_given_as_a_bare_record_works_too() -> None:
    calls = _call(fulfillment.get_order_summary,
                  Client(header={"strCapitalAccId": "7"}), quote_id="Q-1")

    assert calls[-1][1]["strCapitalAccId"] == "7"


def test_a_failed_header_still_sends_the_summary_request() -> None:
    class Broken(Client):
        async def post(self, path, body, auth=None):
            if path == "loadQuoteHeaderDetails":
                raise RuntimeError("down")
            return await super().post(path, body, auth)

    calls = _call(fulfillment.get_order_summary, Broken(), quote_id="Q-1")

    assert calls[-1] == ("loadOrderSummary", {"strQuoteId": "Q-1", "strCapitalAccId": ""})


# ── Dashboard ─────────────────────────────────────────────────────────────

def test_the_dashboard_date_range_uses_the_names_it_reads() -> None:
    calls = _call(quotes.list_quotes, Client(), start_date="07/01/2026", end_date="09/30/2026")
    body = calls[0][1]

    assert body["strQuoteFromDate"] == "07/01/2026"
    assert body["strQuoteToDate"] == "09/30/2026"
    assert "strQuoteStartDate" not in body and "strQuoteExpDate" not in body


# ── Set Allocation ────────────────────────────────────────────────────────

def test_set_allocation_sends_exactly_what_the_screen_sends() -> None:
    calls = _call(allocation.get_set_allocation, Client(), quote_id="Q-1")

    assert calls == [("loadSetAllocationDetails",
                      {"strQuoteId": "Q-1", "strDtFormat": "MM/dd/yyyy"})]


def test_the_confirmation_flag_is_offered_where_the_codebase_lists_it() -> None:
    """Deliberately changed: it was taken off Set Allocation because the browser
    scan never saw fetchConfirmationFlag called. The codebase catalogue lists the
    endpoint on Set Allocation, and no tool is removed from its screen."""
    assert "get_set_allocation_confirmation" in PAGE_TOOLS["setallocation"]


# ── line items ────────────────────────────────────────────────────────────

def test_line_items_name_the_quote_as_the_screen_does() -> None:
    calls = _call(quotes.get_quote_line_items, Client(), quote_id="Q-1")

    assert calls[0][1]["strQuoteId"] == "Q-1"
    assert "strRefId" not in calls[0][1]


def test_an_open_ended_range_is_closed_at_today() -> None:
    """"Since July 1" sent a start and an empty end, and matched nothing. The
    dashboard itself always sends both ends."""
    import datetime as dt

    body = _call(quotes.list_quotes, Client(), start_date="07/01/2026")[0][1]

    assert body["strQuoteFromDate"] == "07/01/2026"
    assert body["strQuoteToDate"] == dt.date.today().strftime("%m/%d/%Y")


def test_a_range_with_only_an_end_gets_an_open_start() -> None:
    body = _call(quotes.list_quotes, Client(), end_date="09/30/2026")[0][1]

    assert body["strQuoteFromDate"] == "01/01/2000"


def test_no_dates_stays_no_dates() -> None:
    body = _call(quotes.list_quotes, Client())[0][1]

    assert body["strQuoteFromDate"] == "" and body["strQuoteToDate"] == ""


def test_the_order_summary_carries_the_quotes_status_and_total() -> None:
    """Live: the orders alone read as "all 4 Shipped" while the portal said "Order
    Process In-Progress" — a $9,840 set was not ordered yet, exactly the gap
    between the quote total ($40,518.25) and the orders ($30,678.25)."""
    class WithOrders(Client):
        async def post(self, path, body, auth=None):
            self.calls.append((path, dict(body)))
            if path == "loadQuoteHeaderDetails":
                return [{"strCapitalAccId": "1", "strQuoteStatusNm": "Order Process In-Progress",
                         "strQuoteTotal": "40518.25"}]
            return {"orderList": "…"}

    out = asyncio.run(fulfillment.get_order_summary(Rt(WithOrders()), quote_id="Q-1"))

    assert out["quoteStatus"] == "Order Process In-Progress"
    assert out["quoteTotals"] == {"strQuoteTotal": "40518.25"}
    assert out["orders"] == {"orderList": "…"}
    assert "not been ordered yet" in out["note"]


def test_an_empty_order_summary_is_left_for_the_empty_result_check() -> None:
    """Wrapping it would hide the emptiness the server's check exists to explain."""
    out = asyncio.run(fulfillment.get_order_summary(Rt(Client()), quote_id="Q-1"))

    assert out == []


def test_crm_calls_carry_the_crm_admin_flag_and_quote_calls_the_dashboards() -> None:
    """The client picks strUserAdminAccessFl by screen, as the portal does."""
    from globus_mcp_server.auth.base import AuthContext, Identity
    from globus_mcp_server.client import GlobusClient

    sent = {}

    class Recording(GlobusClient):
        async def _raw(self, method, path, *, auth, json_body=None):
            sent[path] = json_body
            return {"strStatus": "success", "lstDefault": []}

    auth = AuthContext(identity=Identity(user_id="U", user_admin_access_fl="N",
                                         crm_admin_access_fl="Y"))
    client = Recording()
    asyncio.run(client.post_envelope("crm/task/loadTasks", {}, auth=auth))
    asyncio.run(client.post("loadQuoteDashboardDtls", {}, auth=auth))
    assert sent["crm/task/loadTasks"]["strUserAdminAccessFl"] == "Y"
    assert sent["loadQuoteDashboardDtls"]["strUserAdminAccessFl"] == "N"


def test_order_detail_lines_load_both_grids_as_the_screen_does() -> None:
    """The Order Process screen loads Capital and Metal separately; empty = both."""
    class Lines(Client):
        async def post(self, path, body, auth=None):
            self.calls.append((path, dict(body)))
            return [{"strPartNum": f"{body['strAccountType']}-1"}]

    client = Lines()
    out = asyncio.run(fulfillment.get_categories_by_account_type(Rt(client), quote_id="Q-1"))
    assert client.calls == [
        ("loadCategoryBasedOnAccountType", {"strQuoteId": "Q-1", "strAccountType": "Capital"}),
        ("loadCategoryBasedOnAccountType", {"strQuoteId": "Q-1", "strAccountType": "Metal"})]
    assert [r["accountType"] for r in out] == ["Capital", "Metal"]

    client = Lines()
    asyncio.run(fulfillment.get_categories_by_account_type(Rt(client), quote_id="Q-1",
                                                           account_type="capital"))
    assert [b["strAccountType"] for _, b in client.calls] == ["Capital"]


def test_no_orders_placed_is_explained_not_raised() -> None:
    from globus_mcp_server.client import GlobusApiError

    class NoOrders(Client):
        async def post(self, path, body, auth=None):
            self.calls.append((path, dict(body)))
            if path == "loadOrderSummary":
                raise GlobusApiError("Error while loading Order Summary")
            return [{"strCapitalAccId": "40298", "strQuoteStatusNm": "Quote Confirmed",
                     "strQuoteTotal": "12000"}]

    out = asyncio.run(fulfillment.get_order_summary(Rt(NoOrders()), quote_id="Q-1"))
    assert out["orders"] is None and out["quoteStatus"] == "Quote Confirmed"
    assert "get_categories_by_account_type" in out["note"]


def test_one_account_without_lines_does_not_lose_the_other() -> None:
    """GMQT-103122-1 has Capital lines only; the Metal call errors."""
    from globus_mcp_server.client import GlobusApiError

    class CapitalOnly(Client):
        async def post(self, path, body, auth=None):
            self.calls.append((path, dict(body)))
            if body["strAccountType"] == "Metal":
                raise GlobusApiError("Error while loading category based account type")
            return [{"strPartNum": "6143.2501"}, {"strPartNum": "999.892"}]

    out = asyncio.run(fulfillment.get_categories_by_account_type(Rt(CapitalOnly()), quote_id="Q-1"))
    assert [r["strPartNum"] for r in out["lines"]] == ["6143.2501", "999.892"]
    assert out["total"] == 2 and "Metal" in out["note"]


def test_both_accounts_failing_is_still_an_error() -> None:
    from globus_mcp_server.client import GlobusApiError

    class Down(Client):
        async def post(self, path, body, auth=None):
            raise GlobusApiError("Error while loading category based account type")

    with pytest.raises(GlobusApiError):
        asyncio.run(fulfillment.get_categories_by_account_type(Rt(Down()), quote_id="Q-1"))
