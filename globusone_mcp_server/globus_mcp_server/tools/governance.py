"""Access/security, rule-value, history/audit, and rep-typeahead read tools.

Covers GmUserInfoController, GmSecurityGrpUserController,
GmAccessPermissionsController, GmRuleValueController, GmCommonHistoryController,
GmCommonLogController, GmAuditTrailController, and the sales-rep typeahead from
GmRedisController. Pure auth/session infra (SAML, Okta, getSession) and file
viewers (apiFiles/*) are intentionally excluded — they are not business reads.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from . import Runtime


# -- user / access / security --------------------------------------------
async def get_ad_vp_list(rt: Runtime, company_id: str = "", division: str = "",
                         call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Area Director and VP list (backs rep/AD/VP filters on dashboards)."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "loadADandVPList", {"companyId": company_id, "division": division}, auth=auth)


async def list_security_group_users(rt: Runtime, group_id: str = "", party_id: str = "",
                                    call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Users in a security group."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "fetchSecurityUser", {"groupId": group_id, "partyId": party_id}, auth=auth)


async def get_access_permissions(rt: Runtime, party_id: str, function_id: str,
                                 call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Access-permission flags for a user (party) and application function."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"getAccessPermissions/{party_id}/{function_id}", auth=auth)


# -- rule value -----------------------------------------------------------
async def get_rule_value(rt: Runtime, rule_id: str, company_id: str = "",
                         call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Value of a configured business rule."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "getRuleValue", {"ruleId": rule_id, "companyId": company_id}, auth=auth)


# -- history / log / audit ------------------------------------------------
async def get_common_history_log(rt: Runtime, ref_id: str, ref_type: str = "",
                                 call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Common history-log entries for a reference."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "fetchCommonHistoryLog", {"strRefId": ref_id, "strType": ref_type}, auth=auth)


async def get_call_log(rt: Runtime, ref_id: str, ref_type: str,
                       call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Call / activity log for a reference."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"loadCallLog/{ref_id}/{ref_type}", auth=auth)


async def get_audit_trail(rt: Runtime, ref_id: str, audit_id: str,
                          call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Audit-trail history for a reference."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"loadAuditTrailHistory/{ref_id}/{audit_id}", auth=auth)


# -- rep typeahead (cached) ----------------------------------------------
async def search_sales_reps(rt: Runtime, search: str, company_id: str = "", max_results: str = "20",
                            call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Type-ahead search for sales reps by name."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"loadSalesRepDtls/{search}/{max_results}/{company_id}", auth=auth)
