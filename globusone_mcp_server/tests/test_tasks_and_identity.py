"""The Tasks page can answer about tasks, and "who am I" needs no login.

Live, on Tasks: "Are any tasks under opportunity 2395 assigned to an owner?" The
page offered no task tool at all, so the model reached for what it had and called
`get_user_context(login='')` about twenty times in 35 seconds. It has no way to
know the user's login. An empty login returned nothing, every time.

  * `get_opportunity_tasks` / `get_task_notes` wrap the two endpoints the Tasks
    and Opportunity Detail screens call (crm/task/loadChecklist,
    crm/note/loadTaskNotes).
  * `get_user_context` and `list_companies` fall back to the signed-in user —
    already known from the request's token — when their argument is empty.

Run:  PYTHONPATH=. pytest tests/test_tasks_and_identity.py -q
"""

import asyncio

from globus_mcp_server.auth.base import AuthContext, Identity
from globus_mcp_server.tools import context, opportunities

ME = Identity(user_id="U-1", party_id="P-1", access_level="3", company_id="1000",
              dept_name="Sales")


class Client:
    def __init__(self, replies=None):
        self.gets = []
        self.replies = replies or {}

    async def get(self, path, auth=None):
        self.gets.append(path)
        for prefix, reply in self.replies.items():
            if path.startswith(prefix):
                return reply
        return []


class Rt:
    def __init__(self, client):
        self.client = client

    async def context(self, call_ctx=None):
        return AuthContext(headers={}, cookies={}, identity=ME)


def _run(coro):
    return asyncio.run(coro)


# ── identity ──────────────────────────────────────────────────────────────

def test_an_empty_login_means_the_signed_in_user() -> None:
    client = Client()
    me = _run(context.get_user_context(Rt(client), ""))

    assert me["partyId"] == "P-1" and me["userId"] == "U-1"
    assert client.gets == [], "no lookup with an empty login"


def test_a_named_login_is_still_looked_up() -> None:
    client = Client({"loadUserInfo/jdoe": [{"strLoginName": "jdoe", "strPartyId": "P-9"}]})
    other = _run(context.get_user_context(Rt(client), "jdoe"))

    assert other["partyId"] == "P-9"


def test_companies_default_to_the_signed_in_party() -> None:
    client = Client()
    _run(context.list_companies(Rt(client)))

    assert client.gets[0].startswith("loadCompany/P-1/")


def test_no_identity_is_no_one_not_an_error() -> None:
    class Anonymous(Rt):
        async def context(self, call_ctx=None):
            return AuthContext(headers={}, cookies={}, identity=Identity())

    assert _run(context.get_user_context(Anonymous(Client()), "")) == {}


# ── tasks ─────────────────────────────────────────────────────────────────

def test_an_opportunitys_tasks_come_from_its_checklist() -> None:
    client = Client({"crm/task/loadChecklist/2395": [{"taskId": 7, "owner": "A"}]})
    tasks = _run(opportunities.get_opportunity_tasks(Rt(client), 2395))

    assert tasks == [{"taskId": 7, "owner": "A"}]


def test_no_tasks_on_a_real_opportunity_is_said_plainly() -> None:
    client = Client({"crm/opp/getOpportunityDetail/2395": {"oppId": 2395}})
    out = _run(opportunities.get_opportunity_tasks(Rt(client), 2395))

    assert out["found"] is True and out["tasks"] == []


def test_task_notes_are_read_by_task_id() -> None:
    client = Client({"crm/note/loadTaskNotes/7": [{"note": "called"}]})

    assert _run(opportunities.get_task_notes(Rt(client), 7)) == [{"note": "called"}]
