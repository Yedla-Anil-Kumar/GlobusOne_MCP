"""The "(not set)" deal-confidence filter.

422 of 603 opportunities have no confidence assigned — the largest group in the
pipeline — and the portal's own filter accepts only the five colour values. Asked
for them, the model sent an empty string, received every opportunity back, and
told the user to export the list and filter it by hand.
"""

import asyncio
from typing import Any

import pytest

from globus_mcp_server.tools.opportunities import (
    _has_no_confidence,
    _is_unset_request,
    list_opportunities,
)

PAGE = 200


def _portal_row(i, confidence):
    """A row shaped like the portal's, not like our mapped one — field names taken
    from _ROW_FIELDS so the test breaks if that mapping changes."""
    return {"strOppId": i, "strOppName": f"Deal {i}",
            "strDealConfidence": confidence, "strValue": str(1000 + i)}


class FakeClient:
    """Serves pages of a fixed population, recording the filters it was sent."""

    def __init__(self, population):
        self.population = population
        self.calls = []

    async def post_envelope(self, path, body, auth=None):
        self.calls.append(dict(body))
        size = int(body["strPageSize"])
        page = int(body["strPage"])
        chunk = self.population[(page - 1) * size: page * size]
        return {"strStatus": "S", "strMessage": str(len(self.population)),
                "lstDefault": chunk}


class FakeRuntime:
    def __init__(self, population):
        self.client = FakeClient(population)

    async def context(self, call_ctx=None):
        return {}


def _population(unset, coloured):
    rows = [_portal_row(i, "") for i in range(unset)]
    rows += [_portal_row(1000 + i, "Orange") for i in range(coloured)]
    return rows


def _run(rt, **kwargs):
    return asyncio.run(list_opportunities(rt, **kwargs))


@pytest.mark.parametrize("phrase", [
    "(not set)", "not set", "none", "unset", "unassigned", "blank", "NOT SET", " None ",
])
def test_the_ways_people_and_models_say_not_set_are_recognised(phrase):
    assert _is_unset_request(phrase)


@pytest.mark.parametrize("phrase", ["Orange", "Blue", "", "  ", "Red"])
def test_colours_and_blanks_are_not_unset_requests(phrase):
    """An EMPTY string means "no filter" — the portal's own meaning. Treating it as
    "not set" would silently narrow every unfiltered search."""
    assert not _is_unset_request(phrase)


def test_unset_rows_are_returned_and_counted_correctly():
    rt = FakeRuntime(_population(unset=422, coloured=181))
    out = _run(rt, deal_confidence="(not set)", page_size=50)

    assert out["total"] == 422, "the MATCHING count, not the 603 swept"
    assert out["returned"] == 50
    assert len(out["opportunities"]) == 50
    assert all(_has_no_confidence(r) for r in out["opportunities"])
    assert out["filter"] == "deal confidence not set"
    assert out["sweptRows"] == 603


def test_the_colour_filter_is_dropped_from_the_portal_request():
    """Sending "(not set)" through to `strDealConfidence` would match nothing."""
    rt = FakeRuntime(_population(unset=10, coloured=5))
    _run(rt, deal_confidence="not set")

    assert all(c["strDealConfidence"] == "" for c in rt.client.calls)


def test_other_filters_still_reach_the_portal():
    rt = FakeRuntime(_population(unset=10, coloured=0))
    _run(rt, deal_confidence="(not set)", rsm="Morgan Reyes", stage="Prepare")

    sent = rt.client.calls[0]
    assert sent["strRsm"] == "Morgan Reyes" and sent["strStage"] == "Prepare"


def test_paging_walks_the_filtered_result_not_the_sweep():
    rt = FakeRuntime(_population(unset=422, coloured=181))
    first = _run(rt, deal_confidence="(not set)", page=1, page_size=50)
    second = _run(rt, deal_confidence="(not set)", page=2, page_size=50)

    assert first["opportunities"] != second["opportunities"]
    ids = {r["id"] for r in first["opportunities"]} | {r["id"] for r in second["opportunities"]}
    assert len(ids) == 100, "no row may appear on two pages"


def test_the_requested_sort_is_applied_to_the_filtered_rows():
    rt = FakeRuntime(_population(unset=30, coloured=0))
    out = _run(rt, deal_confidence="(not set)", sort_field="value", sort_dir="desc",
               page_size=10)

    values = [float(r["value"]) for r in out["opportunities"]]
    assert values == sorted(values, reverse=True)


def test_an_ordinary_colour_query_is_untouched():
    """One page, filter passed straight through — no sweep."""
    rt = FakeRuntime([_portal_row(i, "Orange") for i in range(44)])
    out = _run(rt, deal_confidence="Orange", page_size=50)

    assert len(rt.client.calls) == 1
    assert rt.client.calls[0]["strDealConfidence"] == "Orange"
    assert "filter" not in out and out["total"] == 44


def test_no_unset_rows_reports_zero_rather_than_the_sweep_size():
    rt = FakeRuntime(_population(unset=0, coloured=120))
    out = _run(rt, deal_confidence="(not set)")

    assert out["total"] == 0 and out["opportunities"] == []
