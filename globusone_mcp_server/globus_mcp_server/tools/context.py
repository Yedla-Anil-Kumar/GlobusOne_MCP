"""Context & reference tools: user, companies, code lookup."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from . import Runtime


async def get_user_context(rt: Runtime, login: str = "",
                           call_ctx: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """The signed-in user's ids and access level. Leave `login` empty for yourself."""
    auth = await rt.context(call_ctx)
    # The model has no way to know the user's login, and asked with an empty one
    # about twenty times in a row. The signed-in user is already known from the
    # request's token, so an empty login means "me".
    if not str(login or "").strip():
        return _signed_in_user(auth)
    rows = await rt.client.get(f"loadUserInfo/{login}", auth=auth)
    if not rows:
        return {}
    u = rows[0]
    return {
        "login": u.get("strLoginName"),
        "userId": u.get("strUserId"),
        "partyId": u.get("strPartyId"),
        "userName": u.get("strUserName"),
        "companyId": u.get("strCompanyId"),
        "accessLevel": u.get("strAccessLevel"),
        "dept": u.get("strDeptName"),
    }


def _signed_in_user(auth: Any) -> dict[str, Any]:
    identity = getattr(auth, "identity", None)
    if identity is None or not (identity.user_id or identity.party_id):
        return {}
    return {"userId": identity.user_id, "partyId": identity.party_id,
            "companyId": identity.company_id, "accessLevel": identity.access_level,
            "dept": identity.dept_name}


async def list_companies(rt: Runtime, party_id: str = "", company_ids: str = "",
                         call_ctx: Optional[Mapping[str, Any]] = None) -> list[dict[str, Any]]:
    """Companies (entities) visible to a party — yours if `party_id` is left empty."""
    auth = await rt.context(call_ctx)
    party_id = party_id or getattr(getattr(auth, "identity", None), "party_id", "")
    ids = company_ids or "105401,105400"
    rows = await rt.client.get(f"loadCompany/{party_id}/{ids}", auth=auth)
    return [{"companyId": r.get("companyId"), "companyName": r.get("companyName")}
            for r in (rows or [])]


async def lookup_codes(rt: Runtime, code_grp: str,
                       call_ctx: Optional[Mapping[str, Any]] = None) -> list[dict[str, Any]]:
    """Return the id→label entries for a Globus One code group.

    Known groups: CRMSTG (opportunity stages), CRMPRD (product interest),
    CRMDCF (deal confidence), CRMENT (entity types).
    """
    auth = await rt.context(call_ctx)
    rows = await rt.client.post("loadCodeLookup", {"codeGrp": code_grp}, auth=auth)
    return [{"codeId": r.get("codeId"), "codeNm": r.get("codeNm"),
             "codeGrp": r.get("codeGrp")} for r in (rows or [])]
