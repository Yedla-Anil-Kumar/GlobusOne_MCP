"""The model can find out which filter a word belongs to — from the data, not a list.

Live, on Opportunities: "show me closed purple deals" took eight calls. The
portal's filter options list only people; the code lookups for stages, products
and deal confidence return nothing for this login; and nothing told the model
that a word like "closed" is a stage value and "won" a status value. So it
guessed. The values are now learned from the pipeline's own rows, per user.

Run:  PYTHONPATH=. pytest tests/test_learned_filter_values.py -q
"""

import asyncio

from globus_mcp_server.auth.base import AuthContext, Identity
from globus_mcp_server.tools import opportunities as opp

ROWS = [
    {"strOppId": "1", "strStage": "Closed", "strStatus": "Won",
     "strDealConfidence": "Purple", "strProductInterests": "Robot,E3D"},
    {"strOppId": "2", "strStage": "Prepare", "strStatus": "Open",
     "strDealConfidence": "Orange", "strProductInterests": "HUB"},
    {"strOppId": "3", "strStage": "Closed", "strStatus": "Lost",
     "strDealConfidence": "Purple", "strProductInterests": ""},
]


def _matches(row, body):
    for field in ("strStage", "strStatus", "strDealConfidence"):
        want = [v.strip() for v in str(body.get(field) or "").split(",") if v.strip()]
        if want and row[field] not in want:
            return False
    return True


class Client:
    def __init__(self):
        self.bodies = []

    async def post_envelope(self, path, body, auth=None):
        self.bodies.append(dict(body))
        rows = [r for r in ROWS if _matches(r, body)]
        return {"strStatus": "success", "lstDefault": rows, "strMessage": str(len(rows))}

    async def post(self, path, body, auth=None):
        if path == "crm/opp/loadOpportunityFilterOptions":
            return {"rsms": ["A Rep"], "zones": [], "areaDirectors": []}
        return []                                      # code lookups: nothing, as live


class Codes:
    async def ensure_defaults(self, auth=None):
        return None


class Rt:
    def __init__(self):
        self.client, self.codes = Client(), Codes()

    async def context(self, call_ctx=None):
        return AuthContext(headers={}, cookies={}, identity=Identity(user_id="U-learn"))


def setup_function():
    opp._LEARNED_CACHE.clear()


def test_filter_options_list_the_values_the_pipeline_carries() -> None:
    out = asyncio.run(opp.get_filter_options(Rt()))

    assert out["stages"] == ["Closed", "Prepare"]
    assert out["statuses"] == ["Lost", "Open", "Won"]
    assert out["dealConfidences"] == ["Orange", "Purple"]
    assert out["productInterests"] == ["E3D", "HUB", "Robot"]
    assert out["rsms"] == ["A Rep"], "the portal's own people lists are kept"


def test_the_values_are_learned_once_and_reused() -> None:
    rt = Rt()
    asyncio.run(opp.get_filter_options(rt))
    first = len(rt.client.bodies)
    asyncio.run(opp.get_filter_options(rt))

    assert len(rt.client.bodies) == first, "cached, not swept again"


def test_the_live_question_closed_sent_as_a_status_is_corrected_to_the_stage() -> None:
    """With the stage values known, the existing reconciliation can tell that
    "Closed" belongs to stage, and retry — instead of the model guessing."""
    out = asyncio.run(opp.list_opportunities(Rt(), deal_confidence="Purple", status="Closed"))

    ids = sorted(r.get("id") or r.get("oppId") or r.get("strOppId") for r in out["opportunities"])
    assert out["total"] == 2
    assert len(ids) == 2


def test_the_filters_are_described_to_the_model() -> None:
    """What each filter means travels in the tool schema the model reads."""
    from globus_mcp_server import server

    tool = server.mcp._tool_manager.get_tool("list_opportunities")
    props = tool.parameters["properties"]

    assert "sales process" in props["stage"]["description"]
    assert "open or how it ended" in props["status"]["description"]
    assert "get_filter_options" in props["deal_confidence"]["description"]
    assert "get_filter_options" in tool.description
