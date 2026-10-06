"""Reference-data read tools: company, code lookup, territory, division.

Covers GmCompanyController/GmCompanyInfoController, GmCodeLookupController,
GmTerritoryMappingInfoController, GmDivisionController. Endpoints already in
tools/context.py (loadUserInfo, loadCompany, loadCodeLookup) are NOT duplicated.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional
from urllib.parse import quote

from . import Runtime


# -- company --------------------------------------------------------------
async def list_plants(rt: Runtime, company_id: str,
                      call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Plant list for a company."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "loadPlants", {"partyId": auth.identity.party_id, "companyId": company_id}, auth=auth)


async def get_company_info(rt: Runtime, company_id: str,
                           call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Company info (locale, currency, config) for a company id."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"loadCompanyInfo/{company_id}", auth=auth)


# -- code lookup (beyond context.lookup_codes) ----------------------------
async def lookup_codes_with_alt_names(rt: Runtime, code_grp: str,
                                      call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Code list including alternate names for a code group."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("loadCodeNmAltLookup", {"codeGrp": code_grp}, auth=auth)


async def lookup_codes_access_scoped(rt: Runtime, lookup_access: str, code_group: str,
                                     call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Access-scoped code list for a group."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"CodeLookupAccess/{lookup_access}/{code_group}", auth=auth)


async def lookup_codes_excluding(rt: Runtime, code_grp: str, exclude: str = "",
                                 call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Code list for a group with certain codes excluded (comma-separated)."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "loadCodeLookupExclude",
        {"codeGrp": code_grp, "deptId": auth.identity.dept_name,
         "companyId": auth.identity.company_id}, auth=auth)


async def lookup_users(rt: Runtime, code_id: str, company_id: str = "",
                       call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Users under an AD/VP code (the Dashboard's AD/VP pickers)."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "userInfoLookup",
        {"strCodeId": code_id, "strCompanyId": company_id or auth.identity.company_id},
        auth=auth)


_REDIS_KEYS = {"account": "parentAccountRedis", "surgeon": "surgeonNameRedis"}


async def search_accounts(rt: Runtime, search: str, kind: str = "account",
                          max_results: int = 20,
                          call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Find customer accounts (kind="account") or surgeons (kind="surgeon") by name —
    the typeahead the Dashboard, Opportunities and Order Process screens use."""
    auth = await rt.context(call_ctx)
    key = _REDIS_KEYS.get(str(kind or "").strip().lower(), _REDIS_KEYS["account"])
    return await rt.client.get(
        f"loadRedisDtls/{key}/{quote(str(search), safe='')}/{max_results}/"
        f"{auth.identity.company_id}", auth=auth)


# -- territory & division -------------------------------------------------
async def list_divisions_for_company(rt: Runtime, company_id: str,
                                     call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Divisions for a company."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"getDivision/{company_id}", auth=auth)


async def list_zones(rt: Runtime, company_id: str,
                     call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Zones for a company."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"getZones/{company_id}", auth=auth)


async def list_regions(rt: Runtime, company_id: str,
                       call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Regions for a company."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"getRegions/{company_id}", auth=auth)


async def list_all_divisions(rt: Runtime,
                             call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Division master list."""
    auth = await rt.context(call_ctx)
    return await rt.client.get("loadDivision", auth=auth)
