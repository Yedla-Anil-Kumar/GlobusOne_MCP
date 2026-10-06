"""Tasks, opportunities and quotes must cover the companies the portal's grids do.

QA on erptest3: 1 task "In Progress" against the grid's 11, 0 tasks for an
Italian owner with 12, "no opportunities visible" for an RSM whose 5 Dutch deals
were on screen. Every request carried the identity's ONE home company in
strCompanyId (1000, Globus Medical North America), and loadTasks and
loadOpportunityDashboard read that field as a company filter. With the Company
filter set to North America, the grid gave the widget's numbers exactly.

Sending the companies from loadCompany was not the fix: for a live login it
returned only 1000. The grids send no company filter, and empty means none.

On the Dashboard, "quotes where the RSM is Austin Ragans" failed outright: the
name went into strCapitalAdId, which the DAO compares to a numeric rep id.

The fakes behave as the DAOs do: strCompanyId is a CSV filter for tasks and
opportunities and a single company for the dashboard, whose rep-id columns fail
on a name.

Run:  PYTHONPATH=. pytest tests/test_company_scope.py -q
"""

import asyncio

import pytest

from globus_mcp_server import companies
from globus_mcp_server.auth.base import AuthContext, Identity
from globus_mcp_server.tools import crm, opportunities, quotes

ME = Identity(user_id="U-1", party_id="P-1", access_level="3", company_id="1000")
COMPANIES = [{"companyId": 1000, "companyName": "Globus Medical North America"},
             {"companyId": 1020, "companyName": "Globus Medical Italy"},
             {"companyId": 1030, "companyName": "Globus Medical Netherlands"}]

TASKS = [{"strTaskId": "1", "company": "1000"},
         {"strTaskId": "2", "company": "1020"},
         {"strTaskId": "3", "company": "1020"}]
OPPS = [{"strOppId": "10", "strCompanyId": "1000", "strRsmNm": "Jane Doe"},
        {"strOppId": "11", "strCompanyId": "1030", "strRsmNm": "Alan Anthony"}]
QUOTES = [
    {"strQuoteId": "Q-1", "strCompanyId": "1000", "strRsmNm": "Austin Ragans",
     "strSalesRepNm": "Inactive - Austin Ragans", "strZdRsm": "Zac Champion"},
    {"strQuoteId": "Q-2", "strCompanyId": "1000", "strRsmNm": "Jon Callies",
     "strSalesRepNm": "Jon Callies", "strZdRsm": "Jeff Blumenthal"},
    {"strQuoteId": "Q-3", "strCompanyId": "1020", "strRsmNm": "Austin Ragans",
     "strSalesRepNm": "Austin Ragans", "strZdRsm": "Zac Champion"},
]


def _in(row_company, csv):
    return not csv or row_company in csv.split(",")


class Client:
    def __init__(self, companies_reply=COMPANIES):
        self.companies_reply = companies_reply
        self.calls = []

    async def get(self, path, auth=None):
        self.calls.append(("GET", path, {}))
        if isinstance(self.companies_reply, Exception):
            raise self.companies_reply
        return self.companies_reply

    async def post_envelope(self, path, body, auth=None):
        body = {**auth.identity.as_body_params(), **body}
        self.calls.append(("POST", path, body))
        pool, key = (TASKS, "company") if "task" in path else (OPPS, "strCompanyId")
        rows = [r for r in pool if _in(r[key], body["strCompanyId"])]
        return {"strStatus": "success", "strMessage": str(len(rows)), "lstDefault": rows}

    async def post(self, path, body, auth=None):
        body = {**auth.identity.as_body_params(), **body}
        self.calls.append(("POST", path, body))
        if path != "loadQuoteDashboardDtls":
            return {}
        for field in ("strCapitalAdId", "strCapitalVpId"):
            if body.get(field) and not body[field].isdigit():
                raise RuntimeError("ORA-01722: invalid number")
        return [r for r in QUOTES if r["strCompanyId"] == body["strCompanyId"]]


class Rt:
    def __init__(self, client):
        self.client = client

    async def context(self, call_ctx=None):
        return AuthContext(headers={}, cookies={}, identity=ME)


@pytest.fixture(autouse=True)
def _fresh_cache():
    companies.clear_cache()
    yield
    companies.clear_cache()


def _run(fn, client=None, **kwargs):
    client = client or Client()
    return asyncio.run(fn(Rt(client), **kwargs)), client


def _sent(client, path):
    return [body for _m, p, body in client.calls if p == path]


# ── tasks ─────────────────────────────────────────────────────────────────

def test_tasks_send_no_company_filter_by_default() -> None:
    out, client = _run(crm.list_tasks)

    assert _sent(client, "crm/task/loadTasks")[0]["strCompanyId"] == ""
    assert out["total"] == 3, "the Italian tasks are no longer dropped"


def test_no_filter_even_when_loadcompany_knows_only_the_home_company() -> None:
    """The live case: loadCompany gave 1000 alone, the grid had other companies."""
    out, client = _run(crm.list_tasks, client=Client(COMPANIES[:1]))

    assert _sent(client, "crm/task/loadTasks")[0]["strCompanyId"] == ""
    assert out["total"] == 3


def test_a_company_can_be_asked_for_by_part_of_its_name() -> None:
    out, client = _run(crm.list_tasks, company="north america")

    assert _sent(client, "crm/task/loadTasks")[0]["strCompanyId"] == "1000"
    assert out["total"] == 1


def test_several_companies_by_name_or_id() -> None:
    _, client = _run(crm.list_tasks, company="Italy, 1030")

    assert _sent(client, "crm/task/loadTasks")[0]["strCompanyId"] == "1020,1030"


def test_a_company_the_user_does_not_have_is_refused_not_queried() -> None:
    out, client = _run(crm.list_tasks, company="Globus Medical Japan")

    assert _sent(client, "crm/task/loadTasks") == []
    assert out["unmatchedFilters"]["company"]["validValues"] == sorted(
        c["companyName"] for c in COMPANIES)
    assert "do not report this as there being no records" in out["caveat"]


def test_when_the_companies_cannot_be_read_the_default_is_unchanged() -> None:
    out, client = _run(crm.list_tasks, client=Client(RuntimeError("down")))

    assert _sent(client, "crm/task/loadTasks")[0]["strCompanyId"] == ""
    assert out["total"] == 3


def test_one_opportunitys_tasks_are_not_limited_to_the_home_company() -> None:
    _, client = _run(crm.list_opportunity_task_items, opp_id=2395)

    assert _sent(client, "crm/task/loadTasksByEntity")[0]["strCompanyId"] == ""


def test_the_companies_are_read_once_per_user() -> None:
    client = Client()
    asyncio.run(crm.list_tasks(Rt(client), company="Italy"))
    asyncio.run(crm.list_tasks(Rt(client), company="Italy"))

    assert [p for m, p, _ in client.calls if m == "GET"] == ["loadCompany/P-1/105401,105400"]


# ── opportunities ─────────────────────────────────────────────────────────

def test_opportunities_cover_every_company_and_say_which_each_is_in() -> None:
    out, client = _run(opportunities.list_opportunities)

    assert _sent(client, "crm/opp/loadOpportunityDashboard")[0]["strCompanyId"] == ""
    by_id = {r["id"]: r["company"] for r in out["opportunities"]}
    assert by_id == {"10": "Globus Medical North America", "11": "Globus Medical Netherlands"}


def test_opportunities_can_be_narrowed_to_one_company() -> None:
    out, _ = _run(opportunities.list_opportunities, company="Netherlands")

    assert [r["id"] for r in out["opportunities"]] == ["11"]


def test_learned_filter_values_come_from_every_company() -> None:
    opportunities._LEARNED_CACHE.clear()
    _, client = _run(opportunities.get_filter_options)
    opportunities._LEARNED_CACHE.clear()

    sweeps = _sent(client, "crm/opp/loadOpportunityDashboard")
    assert sweeps and all(b["strCompanyId"] == "" for b in sweeps)


# ── the quote dashboard ───────────────────────────────────────────────────

def test_the_dashboard_opens_on_the_home_company_as_the_portal_does() -> None:
    out, client = _run(quotes.list_quotes)

    assert [b["strCompanyId"] for b in _sent(client, "loadQuoteDashboardDtls")] == ["1000"]
    assert [q["quoteId"] for q in out["quotes"]] == ["Q-1", "Q-2"]


def test_several_companies_are_queried_one_at_a_time() -> None:
    """The dashboard DAO compares company_id with `=`, so a CSV would match nothing."""
    out, client = _run(quotes.list_quotes, company="North America,Italy")

    assert [b["strCompanyId"] for b in _sent(client, "loadQuoteDashboardDtls")] == ["1000", "1020"]
    assert out["total"] == 3


def test_an_rsm_name_is_matched_on_the_rows_not_sent_as_a_rep_id() -> None:
    out, client = _run(quotes.list_quotes, rsm="Austin Ragans")

    assert _sent(client, "loadQuoteDashboardDtls")[0]["strCapitalAdId"] == ""
    assert [q["quoteId"] for q in out["quotes"]] == ["Q-1"]
    assert out["quotes"][0]["rsm"] == "Austin Ragans"


def test_a_zone_director_name_works_the_same_way() -> None:
    out, client = _run(quotes.list_quotes, zone_director="zac champion")

    assert _sent(client, "loadQuoteDashboardDtls")[0]["strCapitalVpId"] == ""
    assert [q["quoteId"] for q in out["quotes"]] == ["Q-1"]
    assert out["quotes"][0]["zoneDirector"] == "Zac Champion"


def test_a_rep_id_is_still_sent_to_the_dashboard() -> None:
    _, client = _run(quotes.list_quotes, rsm="30712")

    assert _sent(client, "loadQuoteDashboardDtls")[0]["strCapitalAdId"] == "30712"


def test_an_rsm_on_no_quote_says_so_and_offers_close_names() -> None:
    out, _ = _run(quotes.list_quotes, rsm="Austin Ragan")

    assert out["total"] == 1, "a part of the name matches, as the grid's filter does"

    out, _ = _run(quotes.list_quotes, rsm="Austen Regans")
    assert out["total"] == 0
    assert "Austin Ragans" in out["unmatchedFilters"]["rsm"]["closestValues"]
    assert "do not claim the person does not exist" in out["caveat"]
