"""Page(URI) → tool scoping (NEU-978), owned by the MCP server.

The widget knows which portal page the user is on. To keep the assistant focused
(and safe), the server is the source of truth for *which tools apply to which
page*. The client sends the current page; the server returns the allowed tool
names, and the agent offers only those to the model.

Matching is by the portal's hash-route path (e.g. ``/quote/#/opportunities`` →
``opportunities``). Unknown pages fall back to ``DEFAULT_TOOLS``.

Tool names here MUST match the registered MCP tool (= the tool function name).
"""

from __future__ import annotations

import logging
import re

# stdlib: this package carries no logging dependency.
logger = logging.getLogger(__name__)

# ── the mapping ───────────────────────────────────────────────────────────
#
# Taken from the portal's CURRENT codebase: its catalogue of the 99 read
# endpoints, whose Screen column is verified by a scan of the frontend callers.
# Each group below is one screen and holds the tools whose endpoint that screen
# calls. Tools are only ever ADDED here: one whose endpoint has "no frontend
# caller" keeps the page it had. list_security_group_users is Admin-only, and no
# page is Admin.

# Screens "Global" and "Global search": dropdowns, bootstrap, history/log popups
# and the header search box — on every page.
CONTEXT_TOOLS = (
    "get_user_context", "list_companies", "get_company_info",    # bootstrap
    "lookup_codes", "lookup_codes_with_alt_names",                # code lookups
    "lookup_codes_excluding", "lookup_codes_access_scoped",
    "list_all_divisions", "get_access_permissions", "get_rule_value",
    "get_common_history_log", "get_call_log", "get_audit_trail",
    "search_opportunities", "search_contacts",                    # global search
)

# Screens "Opportunities (list)" + "Opportunity Detail" (one route).
OPPORTUNITY_TOOLS = (
    "list_opportunities", "get_pipeline_analysis",
    "get_filter_options", "get_opportunity_stats",
    "get_opportunity", "get_opportunity_products", "get_opportunity_quotes",
    "get_opportunity_history", "get_opportunity_contacts",
    "get_opportunity_notes", "get_opportunity_activities",
    "get_opportunity_tasks", "get_task_notes",
    "list_opportunity_task_items", "get_task_activities",
    "search_sales_reps", "search_accounts",
    "list_quotes",                          # the Link-Quote modal
    # The Linked-tasks panel calls loadTasks. Without it here, "which tasks are
    # overdue?" asked on this page became one checklist call per opportunity.
    "list_tasks",
)

# Screen "Tasks" (crm/task/*, task notes, task activities, notification prefs).
TASK_TOOLS = (
    "list_tasks", "get_task_stats", "get_task_filter_options", "get_task",
    "get_task_history", "get_task_activities", "get_task_notes",
    "list_opportunity_task_items", "get_opportunity_tasks",
    "get_notification_preferences",
    # Task Detail shows the related record's contacts, notes and activities, and
    # its pickers search reps and accounts.
    "get_opportunity_contacts", "get_opportunity_notes", "get_opportunity_activities",
    "search_sales_reps", "search_accounts",
)

# Screen "Dashboard".
DASHBOARD_TOOLS = (
    "list_quotes", "lookup_users", "search_sales_reps", "search_accounts",
    "list_divisions_for_company", "list_zones", "list_regions", "get_ad_vp_list",
)

# Screen "Metrics".
METRICS_TOOLS = (
    "get_quotes_by_period", "get_quotes_by_rep", "get_quotes_by_status",
    "get_quotes_by_revenue", "get_quote_metrics_report", "list_quote_metric_details",
    "list_robot_types", "lookup_users",
    "list_divisions_for_company", "list_zones", "list_regions", "get_ad_vp_list",
    "search_accounts",
)

# Screen "Create/Edit Quote".
CREATE_QUOTE_TOOLS = (
    "get_quote_header", "get_quote_header_history", "get_quote_line_items",
    "get_quote_category_total", "get_discount_approval", "list_robot_categories",
    "list_alacarte_items", "list_alacarte_durapro_verzera_items", "get_quote_pdf",
    "validate_quote_id", "get_quote_company_id", "get_distributor_type",
    "get_set_part_export", "get_forex_dropdown_values", "validate_forex_rate",
    "get_robot_voltage", "get_part_details", "get_set_details", "search_sales_reps",
    "search_accounts",
)

# Screen "Add Part Set" (route #/edittemplate).
ADD_PART_SET_TOOLS = (
    "list_robot_types", "list_robot_categories", "get_robot_category_details",
    "get_template_details", "get_rule_details", "check_robot_name_exists",
    "get_part_details", "get_set_details",
)

# Screen "Set Allocation".
# The quote-workflow screens each open a quote by validating its id, reading its
# company, and (most) loading its header — so the quote's status is answerable
# on every one of them.
_QUOTE_OPENING = ("get_quote_header", "validate_quote_id", "get_quote_company_id")

SET_ALLOCATION_TOOLS = (
    "get_set_allocation", "get_set_allocation_confirmation", "get_quote_set_details",
    "get_rule_details", "list_plants", *_QUOTE_OPENING,
)

# Screen "Order Process".
ORDER_PROCESS_TOOLS = (
    "get_order_summary", "get_categories_by_account_type", "get_quote_set_details",
    "get_shipping_info", "list_shipping_carriers", "list_shipping_modes",
    "list_addresses", "get_rule_details", "list_plants", "search_accounts",
    *_QUOTE_OPENING,
)

# Screen "AR Process".
AR_PROCESS_TOOLS = (
    "get_po_details", "get_invoiced_report", "list_sold_categories",
    "list_quote_orders", "get_po_comments", "list_plants", *_QUOTE_OPENING,
)

# "Release For Sale"
RELEASE_TOOLS = ("get_release_for_sale", "validate_quote_id", "get_robot_voltage")
# "Track Shipments" — its header view is the header *history* call.
SHIPMENT_TOOLS = ("get_shipping_history", "get_quote_header_history",
                  "validate_quote_id", "get_quote_company_id", "list_plants")
# "InTransit Returns"
INTRANSIT_TOOLS = ("list_in_transit_return_orders", *_QUOTE_OPENING)

# page-slug → tools available on that page (context tools are always added).
# Slugs are the portal's AngularJS hash routes, read from the live address bar.
PAGE_TOOLS: dict[str, tuple[str, ...]] = {
    # The eight sidebar pages.
    "opportunities": OPPORTUNITY_TOOLS,
    "tasks": TASK_TOOLS,
    "dashboard": DASHBOARD_TOOLS,
    "setallocation": SET_ALLOCATION_TOOLS,
    "orderprocess": ORDER_PROCESS_TOOLS,
    "releaseforsale": RELEASE_TOOLS,
    "shippingdetail": SHIPMENT_TOOLS,
    "intransitreturns": INTRANSIT_TOOLS,
    # The portal's LANDING route is "#/" with no segment, and it renders the
    # Dashboard. Without this it resolves to no slug at all and falls through to
    # the default.
    "": DASHBOARD_TOOLS,
    # Screens outside this login's sidebar, mapped for roles that have them.
    "createquote": CREATE_QUOTE_TOOLS,
    "edittemplate": ADD_PART_SET_TOOLS,
    "metrics": METRICS_TOOLS,
    "arprocess": AR_PROCESS_TOOLS,
}

# Default when the page is not in the map above.
#
# Nothing beyond the context tools, which tools_for_page() folds in regardless.
# It used to be OPPORTUNITY_TOOLS, on the reasoning that opportunities are the
# primary question surface and every advertised tool ships its full JSON schema
# on every model request (a wide fallback caused upstream 500s at ~37 tools).
# The size argument still holds; the CHOICE was wrong. It meant an unmapped route
# silently impersonated the opportunities page: asked about opportunities from
# Tasks, or from the "#/" landing route, the assistant answered as though the
# user were looking at them. Page scoping that quietly stops scoping is worse
# than none, because nothing reveals the gap.
#
# An unmapped page now offers lookups only, and says so in the log.
DEFAULT_TOOLS: tuple[str, ...] = ()


def page_slug(uri: str) -> str:
    """Extract the route slug from a portal URL/route.

    Uses the FIRST route segment so parameterised routes still resolve:
    ``.../#/opportunities`` → ``opportunities`` and
    ``.../#/createquote/GMQT-062626-2`` → ``createquote``.
    """
    if not uri:
        return ""
    frag = uri.split("#", 1)[1] if "#" in uri else uri
    for part in frag.split("/"):
        m = re.match(r"[a-zA-Z][a-zA-Z-]+", part)
        if m:
            return m.group(0).lower()
    return ""


def tools_for_page(uri: str) -> list[str]:
    """Return the allowed tool names for the given page URI (context always included)."""
    slug = page_slug(uri)
    page = PAGE_TOOLS.get(slug)
    if page is None:
        # Named, not merely counted: a route we have never mapped is a gap in
        # PAGE_TOOLS, and this line is the only way anyone finds out.
        logger.warning(
            "[scoping] no tools mapped for page %r (from %r) — offering lookups "
            "only. Add it to PAGE_TOOLS if the assistant should work there.",
            slug, uri,
        )
        page = DEFAULT_TOOLS
    seen: dict[str, None] = {}
    for name in (*CONTEXT_TOOLS, *page):
        seen.setdefault(name, None)
    return list(seen.keys())


def mapping() -> dict[str, list[str]]:
    """Full page→tools mapping (context tools folded in). For discovery/debug."""
    return {slug: tools_for_page(f"#/{slug}") for slug in PAGE_TOOLS}
