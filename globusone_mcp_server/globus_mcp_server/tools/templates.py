"""Robot & rule template read tools (GmRobotTemplateController, GmRuleTemplateController)."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from . import Runtime


async def list_robot_types(rt: Runtime,
                           call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """List robot types (template setup)."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("loadRobotDetails", {}, auth=auth)


async def get_robot_category_details(rt: Runtime, robot_master_id: str = "",
                                     call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Robot category details for a robot."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("loadRobotCategorydetails", {"robotMasterId": robot_master_id}, auth=auth)


async def get_template_details(rt: Runtime, robot_master_id: str = "", category_group_id: str = "",
                               call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Template details for selected robot categories."""
    auth = await rt.context(call_ctx)
    # An object, as the Add Part Set screen sends it — not a one-item array.
    return await rt.client.post("loadTemplateDetails", {"robotMasterId": robot_master_id}, auth=auth)


async def get_rule_details(rt: Runtime, rule_id: str = "",
                           call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Rule definitions for the rule-template setup."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("loadRuleDetails", [{"ruleId": rule_id}], auth=auth)
