"""The Tasks screen's own endpoints, with the codebase's field names.

The Tasks page offered only an opportunity's checklist, so "which tasks are
overdue?" or "who owns the tasks on opportunity 2395?" had nothing to call. The
codebase catalogue lists the screen's endpoints (crm/task/*, task activities,
global search, notification preferences) and GmTaskDashboardModel's fields:
strQuickFilter ∈ {open, my-open, due-today, overdue, completed-week},
strCurrentUserId for "my-open", strEntityType + strEntityId for one record's
tasks, and search/detail GETs taking userId + accessLevel as query params.

Run:  PYTHONPATH=. pytest tests/test_crm_task_tools.py -q
"""

import asyncio

from globus_mcp_server.auth.base import AuthContext, Identity
from globus_mcp_server.tools import crm, reference

ME = Identity(user_id="U-1", party_id="P-1", access_level="3", company_id="1000")


class Client:
    def __init__(self, envelope=None):
        self.calls = []
        self.envelope = envelope or {"strStatus": "success", "lstDefault": [], "strMessage": "0"}

    async def post_envelope(self, path, body, auth=None):
        self.calls.append(("POST", path, dict(body)))
        return self.envelope

    async def post(self, path, body, auth=None):
        self.calls.append(("POST", path, dict(body)))
        return {}

    async def get(self, path, auth=None):
        self.calls.append(("GET", path, {}))
        return []


class Rt:
    def __init__(self, client):
        self.client = client

    async def context(self, call_ctx=None):
        return AuthContext(headers={}, cookies={}, identity=ME)


def _run(fn, client=None, **kwargs):
    client = client or Client()
    out = asyncio.run(fn(Rt(client), **kwargs))
    return out, client.calls


# ── the Tasks list ────────────────────────────────────────────────────────

def test_the_task_list_sends_the_models_field_names() -> None:
    _, calls = _run(crm.list_tasks, quick_filter="overdue", owners="O-7",
                    due_from="2026-09-01", due_to="09/30/2026")
    method, path, body = calls[-1]          # after the user's company lookup

    assert (method, path) == ("POST", "crm/task/loadTasks")
    assert body["strQuickFilter"] == "overdue"
    assert body["strOwners"] == "O-7"
    assert body["strDueFrom"] == "09/01/2026", "ISO dates are converted"
    assert body["strDueTo"] == "09/30/2026"
    assert body["strCurrentUserId"] == "U-1", "my-open means the signed-in user"


def test_the_total_comes_from_the_envelope() -> None:
    env = {"strStatus": "success", "strMessage": "42",
           "lstDefault": [{"strTaskId": "1", "strOwnerName": "A"}]}
    out, _ = _run(crm.list_tasks, client=Client(env))

    assert out["total"] == 42 and out["returned"] == 1
    assert out["tasks"][0]["strOwnerName"] == "A"


def test_an_unknown_quick_filter_is_refused_not_sent() -> None:
    """Sent, it would come back empty — which reads as "no tasks"."""
    out, calls = _run(crm.list_tasks, quick_filter="late")

    assert calls == []
    assert out["unmatchedFilters"]["quick_filter"]["validValues"] == list(crm.QUICK_FILTERS)
    assert "do not report this as there being no tasks" in out["caveat"]


# ── one opportunity's tasks ───────────────────────────────────────────────

def test_an_opportunitys_tasks_key_on_its_id_and_the_related_type_name() -> None:
    """GmCrmTaskSqlBuilder: strEntityType is compared to a NUMERIC column, so a
    name there matches nothing. The codebase's own recipe for "tasks under
    opportunity 2395" is strEntityId + strRelatedTypes=Opportunity."""
    _, calls = _run(crm.list_opportunity_task_items, opp_id=2395)
    _, path, body = calls[-1]

    assert path == "crm/task/loadTasksByEntity"
    assert body["strEntityId"] == "2395"
    assert body["strRelatedTypes"] == "Opportunity"
    assert "strEntityType" not in body


def test_no_tasks_points_at_the_checklist_which_is_kept_separately() -> None:
    """task_type='TASK' only — checklist rows are excluded from this query."""
    out, _ = _run(crm.list_opportunity_task_items, opp_id=2395)

    assert "has no tasks visible to you" in out["caveat"]
    assert "get_opportunity_tasks" in out["caveat"]


def test_task_stats_take_no_filters_because_the_dao_ignores_them() -> None:
    """Filters offered there would be dropped, and unfiltered counts read as filtered."""
    import inspect

    params = set(inspect.signature(crm.get_task_stats).parameters) - {"rt", "call_ctx"}
    assert params == set()


# ── GETs with identity query params ───────────────────────────────────────

def test_task_detail_takes_the_user_as_query_params() -> None:
    _, calls = _run(crm.get_task, task_id=7)

    assert calls[0][1] == "crm/task/getTaskDetail/7?userId=U-1&accessLevel=3"


def test_search_is_path_encoded_with_the_user_as_query_params() -> None:
    _, calls = _run(crm.search_opportunities, search="Cleveland Clinic")

    assert calls[0][1] == "crm/search/opportunities/Cleveland%20Clinic?userId=U-1&accessLevel=3"


def test_contacts_search_uses_its_own_path() -> None:
    _, calls = _run(crm.search_contacts, search="Smith")

    assert calls[0][1].startswith("crm/search/contacts/Smith?")


def test_task_history_activities_and_preferences_paths() -> None:
    assert _run(crm.get_task_history, task_id=7, page=2)[1][0][1] == "crm/task/loadChangeHistory/7?page=2"
    assert _run(crm.get_task_activities, task_id=7)[1][0][1] == "crm/activity/loadTaskActivities/7"
    assert _run(crm.get_notification_preferences)[1][0][1] == "crm/notifPref/U-1"


def test_stats_and_filter_options_paths() -> None:
    assert _run(crm.get_task_stats)[1][0][1] == "crm/task/loadTaskStats"
    assert _run(crm.get_task_filter_options)[1][0][1] == "crm/task/loadTaskFilterOptions"


# ── account typeahead ─────────────────────────────────────────────────────

def test_account_search_uses_the_redis_typeahead() -> None:
    _, calls = _run(reference.search_accounts, search="Weston", kind="account")

    assert calls[0][1] == "loadRedisDtls/parentAccountRedis/Weston/20/1000"


def test_surgeon_search_uses_its_key() -> None:
    _, calls = _run(reference.search_accounts, search="Lee", kind="surgeon")

    assert calls[0][1].startswith("loadRedisDtls/surgeonNameRedis/Lee/")
