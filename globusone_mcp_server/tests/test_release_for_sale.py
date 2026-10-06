"""Release For Sale loads by robot or quote, in a country — as its screen does.

Live: "Choose the Robot Voltage as EXCELSIUS GPS AND HUB, 120V and country EU and
give the parts where release for sale is Y" looped through quotes for 38 seconds.
The tool took only raw ids, so the model could not turn the names on screen into
them. The screen (GmReleaseForSaleController) loads its Robot list from
loadRobotVoltageDtls for the user's company and its Country list from code group
RFSALE; rows carry section, setid, setnm, partid, partnm and rfs (Y/N).

Run:  PYTHONPATH=. pytest tests/test_release_for_sale.py -q
"""

import asyncio

from globus_mcp_server.auth.base import AuthContext, Identity
from globus_mcp_server.tools import fulfillment, quotes

ME = Identity(user_id="U", party_id="P", company_id="1000")
ROBOTS = [{"robotMasterId": 11, "robotName": "EXCELSIUS GPS, 120V"},
          {"robotMasterId": 12, "robotName": "EXCELSIUS GPS AND HUB, 120V"}]
COUNTRIES = [{"codeId": "110501", "codeNm": "US"}, {"codeId": "110502", "codeNm": "EU"}]
PARTS = [{"section": "Capital Components", "partid": "6143.1001", "partnm": "System", "rfs": "Y"},
         {"section": "Freight", "partid": "999.892", "partnm": "Shipping", "rfs": "N"},
         {"section": "Service", "partid": "999.890", "partnm": "Warranty", "rfs": "Y"}]


class Client:
    def __init__(self):
        self.calls = []

    async def post(self, path, body, auth=None):
        self.calls.append((path, dict(body)))
        if path == "loadRobotVoltageDtls":
            return ROBOTS
        if path == "loadCodeLookup":
            return COUNTRIES if body.get("codeGrp") == "RFSALE" else []
        return PARTS


class Rt:
    def __init__(self):
        self.client = Client()

    async def context(self, call_ctx=None):
        return AuthContext(identity=ME)


def _run(**kwargs):
    rt = Rt()
    return asyncio.run(fulfillment.get_release_for_sale(rt, **kwargs)), rt.client.calls


def test_the_names_on_screen_are_resolved_as_its_dropdowns_are() -> None:
    out, calls = _run(robot="EXCELSIUS GPS AND HUB, 120V", country="EU")

    assert ("loadRobotVoltageDtls", {"companyId": "1000"}) in calls
    assert ("loadCodeLookup", {"codeGrp": "RFSALE"}) in calls
    assert calls[-1] == ("loadReleaseForSaleDtls",
                         {"strQuoteId": "", "strRobotMasterId": "12", "strCountryId": "110502"})
    assert out == PARTS


def test_only_released_parts_when_asked() -> None:
    out, _ = _run(robot="excelsius gps and hub, 120v", country="eu", release_for_sale="Y")

    assert [p["partid"] for p in out["parts"]] == ["6143.1001", "999.890"]
    assert out["total"] == 2 and out["ofParts"] == 3
    assert out["robot"] == "EXCELSIUS GPS AND HUB, 120V" and out["country"] == "EU"


def test_no_country_is_asked_for_not_guessed() -> None:
    """The screen: "Please Select Any Country". Nothing is queried."""
    out, calls = _run(robot="EXCELSIUS GPS AND HUB, 120V")

    assert out["unmatchedFilters"]["country"]["validValues"] == ["EU", "US"]
    assert not any(p == "loadReleaseForSaleDtls" for p, _ in calls)
    assert "do not look through quotes" in out["caveat"]


def test_a_robot_name_matching_two_robots_is_not_picked() -> None:
    out, calls = _run(robot="EXCELSIUS GPS", country="EU")

    assert "EXCELSIUS GPS AND HUB, 120V" in out["unmatchedFilters"]["robot"]["validValues"]
    assert not any(p == "loadReleaseForSaleDtls" for p, _ in calls)


def test_a_quote_works_in_place_of_a_robot() -> None:
    out, calls = _run(quote_id="GMQT-062326-9", country="EU")

    assert calls[-1][1] == {"strQuoteId": "GMQT-062326-9", "strRobotMasterId": "",
                            "strCountryId": "110502"}


def test_the_robot_list_defaults_to_the_users_company() -> None:
    rt = Rt()
    asyncio.run(quotes.get_robot_voltage(rt))
    assert rt.client.calls == [("loadRobotVoltageDtls", {"companyId": "1000"})]


def test_one_section_is_kept_by_its_name() -> None:
    """Live: "List the part id based on the category of (Capital E3D)" — 190 parts
    reached the model as a 12-row preview, so a section had to be filtered here."""
    out, _ = _run(robot="EXCELSIUS GPS AND HUB, 120V", country="EU", section="capital components")

    assert [p["partid"] for p in out["parts"]] == ["6143.1001"]
    assert out["section"] == "Capital Components" and out["ofParts"] == 3


def test_section_and_flag_together() -> None:
    out, _ = _run(robot="EXCELSIUS GPS AND HUB, 120V", country="EU", section="Service",
                  release_for_sale="N")
    assert out["total"] == 0 and out["section"] == "Service"


def test_a_section_the_parts_do_not_have_lists_the_ones_they_do() -> None:
    out, _ = _run(robot="EXCELSIUS GPS AND HUB, 120V", country="EU", section="Capital E3D")

    assert out["unmatchedFilters"]["section"]["validValues"] == [
        "Capital Components", "Freight", "Service"]
