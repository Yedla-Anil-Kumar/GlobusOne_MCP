"""Filtering by a person the portal displays must not read as "no deals".

The portal showed five opportunities under RSM "Rowan Adler (Italy)". The
agent filtered by exactly that string, the dashboard endpoint matched nothing and
returned zero rows with no error, and the user was told the RSM had no
opportunities at all — a confident wrong answer about live CRM data.

The row we emit carries `strRsmNm` and the filter we accept is `strRsm`; nothing
guaranteed the two share a vocabulary, and nothing reconciled them.
"""

import asyncio
from typing import Any

import pytest

from globus_mcp_server.tools.opportunities import (
    _canonical_person,
    _match_person,
    list_opportunities,
)

# The values loadOpportunityFilterOptions advertises: bare names, no territory.
ADVERTISED = ["Rowan Adler", "Casey Lund", "Jamie Frost"]


def _portal_row(i, rsm):
    return {"strOppId": i, "strOppName": f"Deal {i}", "strRsmNm": rsm,
            "strValue": str(1000 + i), "strDealConfidence": "Orange"}


class FakeClient:
    """Matches strRsm by exact string, as the dashboard endpoint does."""

    def __init__(self, population, advertised=ADVERTISED):
        self.population = population
        self.advertised = advertised
        self.filters = []          # every strRsm the tool sent, in order
        self.options_calls = 0
        self.code_lookups = 0

    async def post_envelope(self, path, body, auth=None):
        # Only filtered queries are attempts. The unfiltered sweep that learns the
        # pipeline's stage/status/confidence values sends no strRsm at all.
        if "strRsm" in body:
            self.filters.append(body.get("strRsm"))
        wanted = body.get("strRsm") or ""
        rows = [r for r in self.population
                if not wanted or r["strRsmNm"] == wanted]
        return {"strStatus": "S", "strMessage": str(len(rows)), "lstDefault": rows}

    async def post(self, path, body, auth=None):
        # Every vocabulary is fetched together now, not just the one the query
        # used — a value can belong to a field other than the one it arrived on.
        # client.post unwraps: a LIST from loadCodeLookup, a DICT from options.
        if path.endswith("loadCodeLookup"):
            self.code_lookups += 1
            return []
        assert path.endswith("loadOpportunityFilterOptions")
        self.options_calls += 1
        return {"rsms": list(self.advertised), "zones": [], "areaDirectors": []}


class FakeRuntime:
    def __init__(self, population, advertised=ADVERTISED):
        self.client = FakeClient(population, advertised)

    async def context(self, call_ctx=None):
        return {}


def _run(rt, **kwargs):
    return asyncio.run(list_opportunities(rt, **kwargs))


# ── matching a name onto the vocabulary the API advertises ────────────────

@pytest.mark.parametrize("shown,expected", [
    ("Rowan Adler (Italy)", "rowan adler"),
    ("Rowan Adler", "rowan adler"),
    ("  Jamie Frost (US - East)  ", "jamie frost"),
])
def test_a_displayed_territory_is_not_part_of_the_name(shown, expected):
    assert _canonical_person(shown) == expected


@pytest.mark.parametrize("supplied", [
    "Rowan Adler (Italy)",   # the string the portal shows, and what broke
    "Rowan Adler",
    "rowan adler",
    "Adler",
])
def test_the_spellings_a_model_would_send_all_resolve(supplied):
    assert _match_person(supplied, ADVERTISED) == "Rowan Adler"


def test_an_exact_advertised_value_is_returned_untouched() -> None:
    """Including one that legitimately contains brackets."""
    options = ["Region One AD-TBH", "Rowan Adler (Italy)"]
    assert _match_person("Rowan Adler (Italy)", options) == "Rowan Adler (Italy)"


def test_an_ambiguous_name_resolves_to_nothing() -> None:
    """Picking one of two Frosts would answer about the wrong person's pipeline."""
    assert _match_person("Frost", ["Jamie Frost", "Robin Frost"]) is None


def test_an_unknown_name_resolves_to_nothing() -> None:
    assert _match_person("Nobody At All", ADVERTISED) is None


# ── the tool itself ───────────────────────────────────────────────────────

def test_the_live_failure_now_returns_the_deals() -> None:
    """Five opportunities, filtered by the string the portal displays."""
    rt = FakeRuntime([_portal_row(i, "Rowan Adler") for i in range(5)])
    out = _run(rt, rsm="Rowan Adler (Italy)")

    assert out["total"] == 5
    assert len(out["opportunities"]) == 5
    assert "unmatchedFilters" not in out
    assert rt.client.filters == ["Rowan Adler (Italy)", "Rowan Adler"], \
        "the first attempt is the caller's spelling; the retry is the resolved one"
    # The reconciliation ran and the retry FOUND the deals — so neither caveat
    # applies. Without this the "recognised but empty" branch can fire over a
    # full result set and tell the reader nothing is visible.
    assert "caveat" not in out
    assert "recognisedButEmpty" not in out


def test_a_matching_query_never_pays_for_the_lookup() -> None:
    """Reconciliation is for empty results only — it must not tax the common case."""
    rt = FakeRuntime([_portal_row(i, "Jamie Frost") for i in range(3)])
    out = _run(rt, rsm="Jamie Frost")

    assert out["total"] == 3
    assert rt.client.options_calls == 0
    assert len(rt.client.filters) == 1


def test_an_unrecognised_name_is_reported_as_such_not_as_an_empty_pipeline() -> None:
    rt = FakeRuntime([_portal_row(i, "Jamie Frost") for i in range(3)])
    out = _run(rt, rsm="Nobody At All")

    assert out["total"] == 0
    unmatched = out["unmatchedFilters"]["rsm"]
    assert unmatched["requested"] == "Nobody At All"
    assert unmatched["validValues"] == ADVERTISED
    assert "not" in out["caveat"].lower()
    assert "no deals" in out["caveat"], "the caveat must forbid the wrong reading"


def test_a_recognised_name_with_no_rows_is_not_called_an_empty_pipeline() -> None:
    """We cannot tell "no deals" from "no deals visible here", so we must not claim.

    Measured live: of the 37 RSMs `loadOpportunityFilterOptions` offers, 12 own
    none of the 483 opportunities the dashboard returns — and the portal's own
    grid lists five deals under one of them. An empty result for a valid name is
    therefore not evidence of an empty pipeline.
    """
    rt = FakeRuntime([_portal_row(1, "Jamie Frost")])
    out = _run(rt, rsm="Casey Lund")

    assert out["total"] == 0
    assert "unmatchedFilters" not in out, "the name was recognised"
    assert out["recognisedButEmpty"] == {"rsm": "Casey Lund"}
    assert "visible" in out["caveat"].lower()
    assert "do NOT state that there are none" in out["caveat"]


def test_a_found_result_carries_no_caveat() -> None:
    """The caveat is for empty results only."""
    rt = FakeRuntime([_portal_row(i, "Jamie Frost") for i in range(3)])
    out = _run(rt, rsm="Jamie Frost")

    assert out["total"] == 3
    assert "caveat" not in out
    assert "recognisedButEmpty" not in out


def test_an_unknown_name_says_unknown_rather_than_not_visible() -> None:
    """Two different failures, two different sentences."""
    rt = FakeRuntime([_portal_row(1, "Jamie Frost")])
    out = _run(rt, rsm="Nobody At All")

    assert "unmatchedFilters" in out
    assert "recognisedButEmpty" not in out


def test_a_query_with_no_filters_at_all_is_untouched() -> None:
    """Nothing supplied, nothing to reconcile — and nothing to pay for."""
    rt = FakeRuntime([])
    out = _run(rt)

    assert out["total"] == 0
    assert rt.client.options_calls == 0
    assert rt.client.code_lookups == 0
    assert "caveat" not in out


def test_the_zone_and_area_filters_get_the_same_treatment() -> None:
    """All three name a person and all three fail the same silent way."""
    class WithZones(FakeClient):
        async def post(self, path, body, auth=None):
            if path.endswith("loadCodeLookup"):
                return {"lstCodeLookUp": []}
            return {"objResult": {"rsms": [], "zones": ["Sky Alderman"],
                                  "areaDirectors": ["Region One AD-TBH"]}}

    for arg, wrong in (("zone_director", "Emery Vaughn"),
                       ("area_director", "Nobody At All")):
        rt = FakeRuntime([])
        rt.client = WithZones([])
        out = _run(rt, **{arg: wrong})
        assert arg in out["unmatchedFilters"], f"{arg} was not checked"


def test_an_empty_published_list_is_not_evidence_the_value_is_wrong() -> None:
    """"I cannot check" must not be reported as "that value does not exist"."""
    rt = FakeRuntime([], advertised=[])
    out = _run(rt, zone_director="Emery Vaughn")

    assert out["total"] == 0
    assert "unmatchedFilters" not in out


def test_a_vocabulary_outage_does_not_break_the_query() -> None:
    """The reconciliation is a diagnosis, not a dependency.

    Deliberately changed: it used to let the error escape, which turned a lookup
    that could not be verified into a failed query. With nothing to check
    against, the honest result is the empty one the CRM actually returned.
    """
    class Broken(FakeClient):
        async def post(self, path, body, auth=None):
            raise RuntimeError("filter options unavailable")

    rt = FakeRuntime([])
    rt.client = Broken([])
    out = _run(rt, rsm="Rowan Adler (Italy)")

    assert out["total"] == 0
    assert "unmatchedFilters" not in out, "an unverifiable name is not a wrong one"
    assert "caveat" not in out
