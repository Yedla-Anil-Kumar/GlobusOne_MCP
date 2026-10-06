"""Every filter with a vocabulary, not just the two that were reported.

The dashboard endpoint answers an unrecognised filter value with ZERO ROWS and no
error, so an empty result is indistinguishable from an empty pipeline. That is
ONE fault, and it applies to every filter the CRM publishes values for.

It reached users twice before being understood as one thing: "Rowan Adler has
no opportunities" beside a portal showing five, then "there are no closed deals"
for a value that was a stage filed under `status`. Each was fixed alone. These
tests cover the ones that had not been reported yet — deal confidence, product
interest and the four date fields — because the next report would have been one
of those.

Run:  PYTHONPATH=. pytest tests/test_filter_reconciliation.py -q
"""

import asyncio

import pytest

from globus_mcp_server.tools.opportunities import (
    _CODE_GROUP_FILTERS,
    _DATE_FILTERS,
    _OPAQUE_FILTERS,
    _PEOPLE_FILTERS,
    _normalise_date,
    list_opportunities,
)

VOCABULARIES = {
    "CRMSTG": ["Prepare", "Build Interest", "Closed"],
    "CRMDCF": ["Blue", "Green", "Orange", "Yellow", "Red", "Purple"],
    "CRMPRD": ["E3D", "Cranial", "Pulse", "EXR", "HUB"],
}
PEOPLE = {"rsms": ["Jamie Frost"], "zones": ["Sky Alderman"],
          "areaDirectors": ["Region One AD-TBH"]}

_FIELD = {"stage": "strStage", "deal_confidence": "strDealConfidence",
          "product_interests": "strProductInterests", "status": "strStatus",
          "expected_close_from": "strExpectedCloseFrom"}


def _row(i, **fields):
    base = {"strOppId": i, "strOppName": f"Deal {i}", "strValue": str(1000 + i)}
    base.update(fields)
    return base


class FakeClient:
    """Exact-string matching on every filter, as the dashboard does."""

    def __init__(self, population):
        self.population = population
        self.bodies = []
        self.lookups = 0

    async def post_envelope(self, path, body, auth=None):
        self.bodies.append(dict(body))
        rows = self.population
        for arg, field in _FIELD.items():
            wanted = body.get(field) or ""
            if wanted:
                rows = [r for r in rows if r.get(field) == wanted]
        return {"strStatus": "S", "strMessage": str(len(rows)), "lstDefault": rows}

    async def post(self, path, body, auth=None):
        self.lookups += 1
        # client.post UNWRAPS the envelope: loadCodeLookup yields the
        # lstCodeLookUp LIST and the filter options yield the objResult DICT.
        # Returning the raw envelopes here is what hid a crash in production.
        if path.endswith("loadCodeLookup"):
            names = VOCABULARIES.get(body.get("codeGrp"), [])
            return [{"codeNm": n} for n in names]
        return dict(PEOPLE)


class FakeRuntime:
    def __init__(self, population):
        self.client = FakeClient(population)

    async def context(self, call_ctx=None):
        return {}


def _run(rt, **kwargs):
    return asyncio.run(list_opportunities(rt, **kwargs))


# ── the class, not the instances ──────────────────────────────────────────

def test_every_filter_is_either_checked_or_deliberately_opaque() -> None:
    """The enumeration itself, so a new filter cannot be added unconsidered.

    Adding a filter to _FILTER_MAP without deciding which of these it belongs to
    is how deal_confidence and product_interests went unchecked for months.
    """
    from globus_mcp_server.tools.opportunities import (
        _FILTER_MAP, _UNVALIDATED_FILTERS,
    )

    accounted = set(_PEOPLE_FILTERS) | set(_CODE_GROUP_FILTERS) \
        | set(_DATE_FILTERS) | set(_OPAQUE_FILTERS) | set(_UNVALIDATED_FILTERS)
    assert set(_FILTER_MAP) == accounted, (
        "a filter belongs to exactly one group: checked against a vocabulary, "
        "date-formatted, unvalidated but movable, or opaque — "
        f"unaccounted: {set(_FILTER_MAP) ^ accounted}"
    )


# ── deal confidence: never covered before ─────────────────────────────────

@pytest.mark.parametrize("supplied", ["orange", "ORANGE", " Orange "])
def test_a_confidence_in_the_wrong_case_still_finds_the_deals(supplied) -> None:
    rt = FakeRuntime([_row(i, strDealConfidence="Orange") for i in range(3)])
    assert _run(rt, deal_confidence=supplied)["total"] == 3


def test_a_confidence_that_does_not_exist_is_reported_not_answered() -> None:
    rt = FakeRuntime([_row(1, strDealConfidence="Orange")])
    out = _run(rt, deal_confidence="Magenta")

    assert out["total"] == 0
    assert out["unmatchedFilters"]["deal_confidence"]["validValues"] == VOCABULARIES["CRMDCF"]
    assert "no deals" in out["caveat"]


# ── product interest: never covered before ────────────────────────────────

def test_a_product_interest_in_the_wrong_case_still_finds_the_deals() -> None:
    rt = FakeRuntime([_row(i, strProductInterests="Cranial") for i in range(2)])
    assert _run(rt, product_interests="cranial")["total"] == 2


def test_an_unknown_product_is_reported_with_the_real_ones() -> None:
    rt = FakeRuntime([_row(1, strProductInterests="Cranial")])
    out = _run(rt, product_interests="Spine")

    assert out["unmatchedFilters"]["product_interests"]["validValues"] == VOCABULARIES["CRMPRD"]


# ── dates: never covered before ───────────────────────────────────────────

@pytest.mark.parametrize("supplied,expected", [
    ("2026-07-24", "07/24/2026"),      # ISO — what a model reaches for
    ("7/4/2026", "07/04/2026"),        # unpadded
    ("07/24/2026", "07/24/2026"),      # already right
])
def test_a_date_is_normalised_to_the_format_the_crm_matches(supplied, expected) -> None:
    assert _normalise_date(supplied) == expected


@pytest.mark.parametrize("junk", ["24 July 2026", "next quarter", "2026", ""])
def test_an_unparseable_date_resolves_to_nothing(junk) -> None:
    assert _normalise_date(junk) is None


def test_an_iso_date_is_retried_in_the_crms_format() -> None:
    rt = FakeRuntime([_row(1, strExpectedCloseFrom="07/24/2026")])
    out = _run(rt, expected_close_from="2026-07-24")

    assert out["total"] == 1
    first, retry = rt.client.bodies
    assert first["strExpectedCloseFrom"] == "2026-07-24"
    assert retry["strExpectedCloseFrom"] == "07/24/2026"


def test_an_unparseable_date_says_what_the_format_is() -> None:
    rt = FakeRuntime([])
    out = _run(rt, expected_close_from="next quarter")

    assert out["unmatchedFilters"]["expected_close_from"]["expectedFormat"] == "MM/DD/YYYY"


def test_a_date_check_costs_no_round_trip() -> None:
    """It needs no vocabulary, so a bad date must not trigger three lookups."""
    rt = FakeRuntime([])
    _run(rt, expected_close_from="next quarter")

    assert rt.client.lookups == 0


# ── misfiling, in any direction ───────────────────────────────────────────

def test_a_stage_sent_as_a_status_is_moved() -> None:
    """The live "closed deals" failure."""
    rt = FakeRuntime([_row(i, strStage="Closed") for i in range(4)])
    out = _run(rt, status="Closed")

    assert out["total"] == 4
    assert rt.client.bodies[-1]["strStage"] == "Closed"
    assert not rt.client.bodies[-1]["strStatus"]


def test_a_confidence_sent_as_a_stage_is_moved() -> None:
    """The same fault in the other direction, which nobody has hit yet."""
    rt = FakeRuntime([_row(i, strDealConfidence="Red") for i in range(2)])
    out = _run(rt, stage="Red")

    assert out["total"] == 2
    assert rt.client.bodies[-1]["strDealConfidence"] == "Red"
    assert not rt.client.bodies[-1]["strStage"]


def test_a_value_is_not_moved_onto_an_occupied_filter() -> None:
    """Overwriting a filter the user did supply would answer a different question."""
    rt = FakeRuntime([_row(1, strStage="Prepare", strDealConfidence="Red")])
    out = _run(rt, stage="Red", deal_confidence="Orange")

    assert out["total"] == 0
    assert all(b.get("strDealConfidence") == "Orange" for b in rt.client.bodies)


# ── cost and safety ───────────────────────────────────────────────────────

def test_a_successful_query_pays_nothing() -> None:
    rt = FakeRuntime([_row(i, strDealConfidence="Orange") for i in range(3)])
    out = _run(rt, deal_confidence="Orange")

    assert out["total"] == 3
    assert rt.client.lookups == 0
    assert len(rt.client.bodies) == 1


def test_the_vocabularies_are_fetched_together_not_one_at_a_time() -> None:
    """Four sequential round trips would make a wrong answer slow as well."""
    import inspect

    from globus_mcp_server.tools.opportunities import _vocabularies

    assert "asyncio.gather" in inspect.getsource(_vocabularies)


def test_an_opaque_filter_is_never_touched() -> None:
    """A free-text account name cannot be wrong, and must not be moved."""
    rt = FakeRuntime([])
    out = _run(rt, account_name="Cedarwood Hospital")

    assert "unmatchedFilters" not in out
    assert rt.client.lookups == 0


def test_a_total_vocabulary_outage_leaves_the_result_alone() -> None:
    class Broken(FakeClient):
        async def post(self, path, body, auth=None):
            raise RuntimeError("lookup unavailable")

    rt = FakeRuntime([])
    rt.client = Broken([])
    out = _run(rt, deal_confidence="Orange")

    assert out["total"] == 0
    assert "unmatchedFilters" not in out


# ── the shape client.post actually returns ────────────────────────────────

def test_a_populated_code_group_does_not_crash_the_query() -> None:
    """The regression that shipped: an AttributeError on every empty result.

    `client.post` unwraps the envelope — loadCodeLookup yields the
    lstCodeLookUp LIST, not the dict around it. `_code_names` read it as a dict,
    so `.get` raised the moment the list was non-empty, and `list_opportunities`
    failed outright on any zero-row filtered query. It passed review and passed
    the tests because the fakes returned the raw envelope: a contract that does
    not exist anywhere in the running system.
    """
    rt = FakeRuntime([])
    out = _run(rt, deal_confidence="Magenta")          # forces the lookups

    assert rt.client.lookups > 0, "the vocabularies were never fetched"
    assert out["total"] == 0
    assert "unmatchedFilters" in out, "the reconciliation ran and reached a verdict"


@pytest.mark.parametrize("payload", [
    [{"codeNm": "Closed"}, {"codeNm": "Prepare"}],     # what post() really gives
    {"lstCodeLookUp": [{"codeNm": "Closed"}]},         # an unwrap fallback
    [],
    {},
    None,
    "nonsense",
    [{"noNameHere": 1}, None],
])
def test_every_payload_shape_is_survivable(payload) -> None:
    """A vocabulary fetch must never raise into the query it was checking."""
    from globus_mcp_server.tools.opportunities import _code_names

    class Client:
        async def post(self, path, body, auth=None):
            return payload

    class RT:
        client = Client()

    names = asyncio.run(_code_names(RT(), None, "CRMSTG"))
    assert isinstance(names, list)
    assert all(isinstance(n, str) for n in names)


def test_a_raising_vocabulary_cannot_fail_the_query() -> None:
    """gather(return_exceptions) — the query must survive an unreachable group."""
    class Exploding(FakeClient):
        async def post(self, path, body, auth=None):
            raise RuntimeError("code lookup down")

    rt = FakeRuntime([])
    rt.client = Exploding([])
    out = _run(rt, deal_confidence="Orange")

    assert out["total"] == 0
    assert "unmatchedFilters" not in out, "unverifiable is not the same as invalid"
