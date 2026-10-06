"""A stage value filed under `status` must still find the deals.

Live failure on the customer's portal: "Show me all closed deals" answered "There
are no closed deals in the pipeline" while the CRM held plenty — the same portal
session had just listed deals at stage Closed. The model sent
`status="Closed"`; `strStatus` matched nothing; zero rows came back with no
error; and an empty result read as an empty pipeline.

"Closed" is the last of the seven CRMSTG stages, not a status. Only one of the
two fields has a published vocabulary, which is exactly what makes this
correctable: a value provably in the stage list was misfiled, and anything else
is left alone.
"""

import asyncio
from typing import Any

import pytest

from globus_mcp_server.tools.opportunities import (
    _match_code,
    _stage_names,
    list_opportunities,
)

STAGES = ["Prepare", "Ask Priorities", "Build Interest", "Create Solutions",
          "Drive Alignment", "Execute and Deliver", "Closed"]


def _portal_row(i, stage, status="Active"):
    return {"strOppId": i, "strOppName": f"Deal {i}", "strStage": stage,
            "strStatus": status, "strValue": str(1000 + i)}


class FakeClient:
    """Matches strStage and strStatus by exact string, as the dashboard does."""

    def __init__(self, population):
        self.population = population
        self.bodies = []
        self.code_lookups = 0

    async def post_envelope(self, path, body, auth=None):
        self.bodies.append(dict(body))
        stage = body.get("strStage") or ""
        status = body.get("strStatus") or ""
        rows = [
            r for r in self.population
            if (not stage or r["strStage"] == stage)
            and (not status or r["strStatus"] == status)
        ]
        return {"strStatus": "S", "strMessage": str(len(rows)), "lstDefault": rows}

    async def post(self, path, body, auth=None):
        # client.post unwraps: a LIST from loadCodeLookup, a DICT from options.
        if path.endswith("loadCodeLookup"):
            self.code_lookups += 1
            names = STAGES if body.get("codeGrp") == "CRMSTG" else []
            return [{"codeNm": n} for n in names]
        return {"rsms": [], "zones": [], "areaDirectors": []}


class FakeRuntime:
    def __init__(self, population):
        self.client = FakeClient(population)

    async def context(self, call_ctx=None):
        return {}


def _run(rt, **kwargs):
    return asyncio.run(list_opportunities(rt, **kwargs))


# ── matching a value onto the stage vocabulary ────────────────────────────

@pytest.mark.parametrize("supplied", ["Closed", "closed", "CLOSED", " Closed "])
def test_the_spellings_a_model_would_send_all_resolve(supplied):
    assert _match_code(supplied, STAGES) == "Closed"


def test_a_multi_word_stage_survives_odd_spacing():
    assert _match_code("execute  and   deliver", STAGES) == "Execute and Deliver"


@pytest.mark.parametrize("supplied", ["Won", "Lost", "Active", "", "   "])
def test_a_value_that_is_not_a_stage_resolves_to_nothing(supplied):
    assert _match_code(supplied, STAGES) is None


def test_the_vocabulary_comes_from_the_crm_not_from_us():
    """A hardcoded list goes stale the day they add a stage."""
    rt = FakeRuntime([])
    assert asyncio.run(_stage_names(rt, auth=None)) == STAGES
    assert rt.client.code_lookups == 1


# ── the tool itself ───────────────────────────────────────────────────────

def test_the_live_failure_now_returns_the_closed_deals() -> None:
    """"Show me all closed deals", filed by the model under status."""
    rt = FakeRuntime(
        [_portal_row(i, "Closed") for i in range(4)]
        + [_portal_row(10 + i, "Prepare") for i in range(3)]
    )
    out = _run(rt, status="Closed")

    assert out["total"] == 4
    assert len(out["opportunities"]) == 4
    first, retry = rt.client.bodies
    assert first["strStatus"] == "Closed" and not first["strStage"]
    assert retry["strStage"] == "Closed", "the retry moves it to the stage filter"
    assert not retry["strStatus"], "and clears the field it did not belong in"


def test_a_stage_filter_that_already_works_is_untouched() -> None:
    rt = FakeRuntime([_portal_row(i, "Closed") for i in range(4)])
    out = _run(rt, stage="Closed")

    assert out["total"] == 4
    assert len(rt.client.bodies) == 1, "no retry needed"
    assert rt.client.code_lookups == 0, "and no lookup paid for"


def test_a_misspelled_stage_is_corrected() -> None:
    rt = FakeRuntime([_portal_row(i, "Execute and Deliver") for i in range(2)])
    out = _run(rt, stage="execute and deliver")

    assert out["total"] == 2


def test_a_stage_that_does_not_exist_is_reported_with_the_valid_ones() -> None:
    rt = FakeRuntime([_portal_row(1, "Prepare")])
    out = _run(rt, stage="Won")

    assert out["total"] == 0
    assert out["unmatchedFilters"]["stage"] == {"requested": "Won", "validValues": STAGES}
    assert "no deals" in out["caveat"]


def test_a_genuine_status_filter_is_left_alone() -> None:
    """Only a value provably in the stage list moves; a real status must not."""
    rt = FakeRuntime([_portal_row(1, "Prepare", status="Inactive")])
    out = _run(rt, status="Archived")

    assert out["total"] == 0
    assert all(b.get("strStatus") == "Archived" for b in rt.client.bodies)
    assert "unmatchedFilters" not in out, "we cannot prove a status is wrong"


def test_a_successful_query_never_pays_for_the_lookup() -> None:
    rt = FakeRuntime([_portal_row(i, "Prepare") for i in range(3)])
    out = _run(rt, stage="Prepare")

    assert out["total"] == 3
    assert rt.client.code_lookups == 0


def test_a_query_with_no_filters_at_all_is_untouched() -> None:
    rt = FakeRuntime([])
    _run(rt)

    assert rt.client.code_lookups == 0


def test_a_stage_lookup_outage_leaves_the_result_as_it_was() -> None:
    """The reconciliation is a repair, not a dependency."""
    class NoCodes(FakeClient):
        async def post(self, path, body, auth=None):
            return {}

    rt = FakeRuntime([])
    rt.client = NoCodes([])
    out = _run(rt, status="Closed")

    assert out["total"] == 0
    assert "unmatchedFilters" not in out
