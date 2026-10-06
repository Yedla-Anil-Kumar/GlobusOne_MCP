"""An empty per-opportunity lookup must say what it means.

Live: "What's the status and stage of opportunity 2395?" — `get_opportunity`
returned `[]`, because the API answers an unknown or invisible id with an error
status and GlobusClient.unwrap turns that into an empty list. The model could not
tell "does not exist" from "not visible to you" from "failed". It went searching
200 rows of `list_opportunities` for the id — a tool that cannot filter by id —
repeated, was locked out of tools, wrote its next call as text, and the user got
a generic error.

The follow-up was worse: "What quotes are linked to opportunity 2395?" answered
"has no quotes linked to it yet" — a confident claim resting on an empty result
that meant only that nothing came back. If the opportunity is invisible, EVERY
lookup hanging off it returns `[]`, and each reads as "this one has none".

Run:  PYTHONPATH=. pytest tests/test_opportunity_not_found.py -q
"""

import asyncio

import pytest

from globus_mcp_server.tools.opportunities import (
    get_opportunity,
    get_opportunity_activities,
    get_opportunity_contacts,
    get_opportunity_history,
    get_opportunity_notes,
    get_opportunity_products,
    get_opportunity_quotes,
)

DETAIL = {"ID": 272, "OPPORTUNITYNAME": "Westbrook South", "STATUS": "Won",
          "STAGEID": 262420128, "STAGENAME": "Closed"}


class FakeCodes:
    async def ensure_defaults(self, auth=None):
        return None

    def relabel(self, record, fields):
        return dict(record)


class FakeClient:
    """`get` returns what GlobusClient.unwrap returns: `[]` for not-found."""

    def __init__(self, detail=None, sub=None):
        self.detail = detail          # getOpportunityDetail result
        self.sub = sub if sub is not None else []
        self.paths = []

    async def get(self, path, auth=None):
        self.paths.append(path)
        if "getOpportunityDetail" in path:
            return self.detail if self.detail is not None else []
        return self.sub


class FakeRuntime:
    def __init__(self, **kwargs):
        self.client = FakeClient(**kwargs)
        self.codes = FakeCodes()

    async def context(self, call_ctx=None):
        return {}


def _run(coro):
    return asyncio.run(coro)


# ── the detail lookup ─────────────────────────────────────────────────────

def test_a_visible_opportunity_returns_its_detail() -> None:
    rt = FakeRuntime(detail=DETAIL)
    out = _run(get_opportunity(rt, 272))

    assert out["STAGENAME"] == "Closed"
    assert out["STATUS"] == "Won"
    assert "found" not in out, "a real record is passed through, not wrapped"


def test_an_invisible_opportunity_says_so_instead_of_returning_nothing() -> None:
    """The live failure: `[]`, with nothing to say what it meant."""
    rt = FakeRuntime(detail=None)
    out = _run(get_opportunity(rt, 2395))

    assert out["found"] is False
    assert out["oppId"] == 2395
    assert "visible" in out["caveat"]


def test_the_caveat_stops_the_search_that_caused_the_loop() -> None:
    """The model scanned list_opportunities for the id — it cannot filter by id."""
    out = _run(get_opportunity(FakeRuntime(detail=None), 2395))

    assert "Do NOT search list_opportunities" in out["caveat"]


def test_an_empty_dict_is_not_mistaken_for_a_record() -> None:
    """Some unwrap fallbacks return the envelope minus its status fields: `{}`."""
    out = _run(get_opportunity(FakeRuntime(detail={}), 2395))

    assert out["found"] is False


# ── the lookups hanging off one opportunity ───────────────────────────────

SUB_TOOLS = [
    (get_opportunity_quotes, "quotes"),
    (get_opportunity_products, "products"),
    (get_opportunity_activities, "activities"),
    (get_opportunity_contacts, "contacts"),
    (get_opportunity_notes, "notes"),
    (get_opportunity_history, "history"),
]


@pytest.mark.parametrize("tool,what", SUB_TOOLS)
def test_an_invisible_opportunity_is_not_reported_as_having_none(tool, what) -> None:
    """"Has no quotes linked to it yet" — for an opportunity nobody could see."""
    rt = FakeRuntime(detail=None, sub=[])
    out = _run(tool(rt, 2395))

    assert out["found"] is False, f"{what}: an empty list read as an empty {what}"
    assert "visible" in out["caveat"]


@pytest.mark.parametrize("tool,what", SUB_TOOLS)
def test_a_visible_opportunity_with_none_says_so_plainly(tool, what) -> None:
    """Confirmed to exist, genuinely empty — THAT is when "has none" is true."""
    rt = FakeRuntime(detail=DETAIL, sub=[])
    out = _run(tool(rt, 272))

    assert out["found"] is True
    assert out[what] == []
    assert f"has no {what}" in out["caveat"]


@pytest.mark.parametrize("tool,what", SUB_TOOLS)
def test_a_populated_result_is_untouched_and_costs_nothing_extra(tool, what) -> None:
    """The existence check runs only on an empty result."""
    rows = [{"id": 1}, {"id": 2}]
    rt = FakeRuntime(detail=DETAIL, sub=rows)
    out = _run(tool(rt, 272))

    assert out == rows
    assert not any("getOpportunityDetail" in p for p in rt.client.paths), \
        "a result with rows must not pay for an existence check"


def test_history_keeps_its_paging() -> None:
    """Routing it through the shared check must not drop page and pageSize."""
    rt = FakeRuntime(detail=DETAIL, sub=[{"field": "stage"}])
    _run(get_opportunity_history(rt, 272, page=3, page_size=25))

    assert rt.client.paths[0].endswith("/272?page=3&pageSize=25")


def test_an_existence_check_that_fails_does_not_claim_the_opportunity_exists() -> None:
    """Unverifiable must not become "exists and has none"."""
    class Flaky(FakeClient):
        async def get(self, path, auth=None):
            if "getOpportunityDetail" in path:
                raise RuntimeError("detail endpoint down")
            return []

    rt = FakeRuntime()
    rt.client = Flaky()
    out = _run(get_opportunity_quotes(rt, 2395))

    assert out["found"] is False
