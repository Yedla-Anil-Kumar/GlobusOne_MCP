"""Quote filters must not answer "none" for a value the dashboard spells differently.

Live, on the Dashboard page: "How many GMNA quotes are Approved vs. Draft since
July 1, 2026?" answered "There are 0 Approved and 0 Draft" — while an unfiltered
call in the same turn had returned a thousand quotes. Three separate faults, each
silent on its own:

  * `status="Approved"` — the dashboard's status filter appears to want the
    status ID (rows carry "110817" alongside "Approved"), so a name matched
    nothing.
  * `start_date="2026-07-01"` — the dashboard speaks MM/DD/YYYY and answers an
    ISO date with zero rows, the same mismatch the opportunity filters had.
    (It also reads the range from strQuoteFromDate/strQuoteToDate; the fake
    below filters on that name, so a request under any other name finds all.)
  * the row mapping read `strQuoteStatus`, which the dashboard returns null on
    every row. The model held a thousand quotes with no status on any of them,
    so it could not count them even by hand.

The fixture rows below are shaped exactly like the real dashboard's: the
null `strQuoteStatus`, the populated `strQuoteStatusNm` / `strQuoteStatusId`.

Run:  PYTHONPATH=. pytest tests/test_quote_filters.py -q
"""

import asyncio

import pytest

from globus_mcp_server.tools.quotes import _trim_quote, list_quotes

STATUS_IDS = {"Approved": "110817", "Draft": "110814", "Cancelled": "110825"}


def _quote(i, status, start="07/15/2026"):
    return {"strQuoteId": f"Q-{i}", "strQuoteStatus": None,
            "strQuoteStatusNm": status, "strQuoteStatusId": STATUS_IDS[status],
            "strQuoteStartDate": start, "strQuoteTotal": str(1000 * i)}


POPULATION = ([_quote(i, "Approved") for i in range(12)]
              + [_quote(20 + i, "Draft") for i in range(4)]
              + [_quote(40 + i, "Cancelled") for i in range(5)])


def _ymd(us_date):
    month, day, year = us_date.split("/")
    return (int(year), int(month), int(day))


class FakeClient:
    """Status matched by ID, dates as MM/DD/YYYY and filtered from — as the real
    dashboard behaves. A fake that only REJECTED ISO dates, without filtering on
    valid ones, is exactly how a wrong contract hides a bug."""

    def __init__(self, population=POPULATION):
        self.population = population
        self.bodies = []

    async def post(self, path, body, auth=None):
        assert path == "loadQuoteDashboardDtls"
        self.bodies.append(dict(body))
        rows = self.population
        status = body.get("strQuoteStatus") or ""
        if status:
            rows = [r for r in rows if r["strQuoteStatusId"] == status]
        start = body.get("strQuoteFromDate") or ""
        if start:
            if not (len(start) == 10 and start[2] == "/" and start[5] == "/"):
                return []                              # an ISO date: nothing matches
            rows = [r for r in rows if _ymd(r["strQuoteStartDate"]) >= _ymd(start)]
        return rows


class FakeRuntime:
    def __init__(self, population=POPULATION):
        self.client = FakeClient(population)

    async def context(self, call_ctx=None):
        return {}


def _run(rt, **kwargs):
    return asyncio.run(list_quotes(rt, **kwargs))


# ── the row the model reads ───────────────────────────────────────────────

def test_a_quote_row_carries_its_status_name() -> None:
    """The dashboard's `strQuoteStatus` is null on every row; the name is elsewhere."""
    row = _trim_quote(_quote(1, "Approved"))

    assert row["status"] == "Approved"
    assert row["statusId"] == "110817"


def test_an_unfiltered_list_lets_the_model_count_by_status() -> None:
    """The live turn held 1000 quotes and could not count any of them."""
    out = _run(FakeRuntime())
    statuses = [q["status"] for q in out["quotes"]]

    assert statuses.count("Approved") == 12
    assert statuses.count("Draft") == 4


# ── the live question ─────────────────────────────────────────────────────

@pytest.mark.parametrize("status,expected", [("Approved", 12), ("Draft", 4)])
def test_the_live_question_now_finds_the_quotes(status, expected) -> None:
    """"Approved vs. Draft since July 1" — ISO date, status name."""
    out = _run(FakeRuntime(), status=status, start_date="2026-07-01")

    assert out["total"] == expected
    assert all(q["status"] == status for q in out["quotes"])
    assert "caveat" not in out


def test_an_iso_date_is_sent_in_the_dashboards_format() -> None:
    rt = FakeRuntime()
    _run(rt, start_date="2026-07-01")

    assert rt.client.bodies[0]["strQuoteFromDate"] == "07/01/2026"


def test_a_status_name_is_sent_as_its_id_and_never_as_the_name() -> None:
    """Deliberately changed: the name used to be tried first. The DAO's filter is
    an IN list of numeric ids, and live, names in it made the dashboard error."""
    rt = FakeRuntime()
    _run(rt, status="Approved")

    sent = [b.get("strQuoteStatus") for b in rt.client.bodies]
    assert "Approved" not in sent
    assert sent[-1] == "110817"


def test_the_status_name_is_matched_ignoring_case() -> None:
    assert _run(FakeRuntime(), status="approved")["total"] == 12


# ── what must not change ──────────────────────────────────────────────────

def test_a_query_that_already_works_costs_no_extra_call() -> None:
    """A status ID passed straight through finds the rows first time."""
    rt = FakeRuntime()
    out = _run(rt, status="110817")

    assert out["total"] == 12
    assert len(rt.client.bodies) == 1


def test_a_correct_date_is_left_alone() -> None:
    rt = FakeRuntime()
    _run(rt, start_date="07/01/2026")

    assert rt.client.bodies[0]["strQuoteFromDate"] == "07/01/2026"


# ── honest when it cannot help ────────────────────────────────────────────

def test_an_unknown_status_names_the_ones_that_exist() -> None:
    out = _run(FakeRuntime(), status="Shipped Twice")

    assert out["total"] == 0
    assert out["unmatchedFilters"]["status"]["validValues"] == sorted(STATUS_IDS)
    assert "Approved, Cancelled, Draft" in out["caveat"]


def test_an_unknown_status_is_not_claimed_to_not_exist() -> None:
    """Learned from the data, so a real status with no quotes looks the same as a
    typo. "No quote has it" is true either way; "it does not exist" may not be."""
    out = _run(FakeRuntime(), status="Shipped Twice")

    assert "No quote currently has the status" in out["caveat"]
    assert "do not claim the status does not exist" in out["caveat"]


def test_an_unparseable_date_says_what_the_format_is() -> None:
    out = _run(FakeRuntime(), start_date="since last summer")

    assert out["unmatchedFilters"]["start_date"]["expectedFormat"] == "MM/DD/YYYY"
    assert "do NOT report this as there being no quotes" in out["caveat"]


def test_a_real_status_with_no_quotes_reads_as_none_not_as_a_typo() -> None:
    """Draft is present in the vocabulary but has no quotes in the requested
    range: resolved, retried as its id, genuinely empty — no caveat at all."""
    rt = FakeRuntime()
    out = _run(rt, status="Draft", start_date="01/01/2030")

    assert out["total"] == 0
    assert "unmatchedFilters" not in out
    assert "caveat" not in out


def test_a_vocabulary_outage_does_not_break_the_query() -> None:
    class Broken(FakeClient):
        async def post(self, path, body, auth=None):
            if not body:
                raise RuntimeError("dashboard down")
            return []

    rt = FakeRuntime()
    rt.client = Broken()
    out = _run(rt, status="Approved")

    assert out["total"] == 0
    assert "unmatchedFilters" not in out, "unverifiable is not the same as invalid"


# ── a near-miss status name ───────────────────────────────────────────────
# Live: "give the quote where the status is in Quote Stage" answered "no quote
# has the status Quote Stage" — and listed "Quote Staged" among the valid ones.

NEAR = ([_quote(i, "Approved") for i in range(2)] + [dict(_quote(9, "Draft"),
        strQuoteStatusNm="Quote Staged", strQuoteStatusId="110900")]
        + [dict(_quote(10 + i, "Draft"), strQuoteStatusNm=n, strQuoteStatusId=c)
           for i, (n, c) in enumerate([("Pending Approval", "110901"),
                                       ("Pending Shipping", "110902")])])


def test_a_near_miss_status_is_read_as_the_one_it_plainly_means() -> None:
    rt = FakeRuntime(NEAR)
    out = asyncio.run(list_quotes(rt, status="Quote Stage"))

    assert [q["quoteId"] for q in out["quotes"]] == ["Q-9"]
    assert out["statusMatchedAs"] == {"Quote Stage": "Quote Staged"}
    assert "say so" in out["note"]


def test_a_name_that_could_mean_two_statuses_is_not_guessed() -> None:
    out = asyncio.run(list_quotes(FakeRuntime(NEAR), status="Pending"))

    assert out["total"] == 0
    assert "Pending Approval" in out["unmatchedFilters"]["status"]["validValues"]
    assert "statusMatchedAs" not in out
