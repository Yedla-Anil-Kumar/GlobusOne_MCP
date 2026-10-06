"""Each page offers the tools whose endpoints that page's screen actually calls.

The mapping used to be inferred from endpoint names, and it drifted: Dashboard
offered the quote header and line items (Create Quote's calls) and six Metrics
reports; Order Process offered the AR tools; Track Shipments offered Order
Process's shipping lookups. A small model picks from what it is offered, so
tools the screen never uses meant wasted calls and empty results.

It is now taken from the portal's current codebase catalogue: every read
endpoint with the screen(s) that call it. These tests pin each page to that
Screen column. No tool is removed; each is offered where its screen is.

Run:  PYTHONPATH=. pytest tests/test_page_mapping_from_portal.py -q
"""

import pytest

from globus_mcp_server import server
from globus_mcp_server.scoping import CONTEXT_TOOLS, PAGE_TOOLS, tools_for_page

REGISTERED = {t.name for t in server.mcp._tool_manager.list_tools()}

# Screen → tools, as the extract assigns them (context/"Global" tools excluded).
OPENING = {"get_quote_header", "validate_quote_id", "get_quote_company_id"}

FROM_THE_EXTRACT = {
    "dashboard": {"list_quotes", "lookup_users", "search_sales_reps", "search_accounts",
                  "list_divisions_for_company", "list_zones", "list_regions",
                  "get_ad_vp_list"},
    "setallocation": {"get_set_allocation", "get_set_allocation_confirmation",
                      "get_quote_set_details", "get_rule_details", "list_plants"} | OPENING,
    "orderprocess": {"get_order_summary", "get_categories_by_account_type",
                     "get_quote_set_details", "get_shipping_info",
                     "list_shipping_carriers", "list_shipping_modes",
                     "list_addresses", "get_rule_details", "list_plants",
                     "search_accounts"} | OPENING,
    "tasks": {"list_tasks", "get_task_stats", "get_task_filter_options", "get_task",
              "get_task_history", "get_task_activities", "get_task_notes",
              "list_opportunity_task_items", "get_opportunity_tasks",
              "get_notification_preferences", "get_opportunity_contacts",
              "get_opportunity_notes", "get_opportunity_activities",
              "search_sales_reps", "search_accounts"},
    "releaseforsale": {"get_release_for_sale", "validate_quote_id", "get_robot_voltage"},
    "shippingdetail": {"get_shipping_history", "get_quote_header_history",
                       "validate_quote_id", "get_quote_company_id", "list_plants"},
    "intransitreturns": {"list_in_transit_return_orders"} | OPENING,
    "arprocess": {"get_po_details", "get_invoiced_report", "list_sold_categories",
                  "list_quote_orders", "get_po_comments", "list_plants"} | OPENING,
}


@pytest.mark.parametrize("slug", sorted(FROM_THE_EXTRACT))
def test_each_page_offers_exactly_its_screens_tools(slug) -> None:
    assert set(PAGE_TOOLS[slug]) == FROM_THE_EXTRACT[slug]


def test_every_mapped_name_is_a_real_tool() -> None:
    """A misspelt name is silently never offered."""
    for slug, tools in PAGE_TOOLS.items():
        unknown = set(tools) - REGISTERED
        assert not unknown, f"{slug!r} maps tools that do not exist: {unknown}"
    assert not set(CONTEXT_TOOLS) - REGISTERED


def test_the_global_screen_tools_are_on_every_page() -> None:
    for slug in PAGE_TOOLS:
        assert set(CONTEXT_TOOLS) <= set(tools_for_page(f"#/{slug}"))


def test_the_backends_sentinels_stay_global() -> None:
    """The backend treats none of these present as "the MCP toolset failed"."""
    assert {"get_user_context", "list_companies", "lookup_codes"} <= set(CONTEXT_TOOLS)


def test_every_tool_is_offered_somewhere_except_the_named_few() -> None:
    """A tool on no page is dead weight — unless left off deliberately."""
    offered = set(CONTEXT_TOOLS).union(*PAGE_TOOLS.values())
    deliberate = {"list_security_group_users",     # Admin screen only
                  "get_tools_for_page", "get_tool_page_mapping"}
    assert REGISTERED - offered == deliberate


# ── what moved ────────────────────────────────────────────────────────────

def test_quotes_are_listed_where_the_codebase_calls_the_dashboard_query() -> None:
    """The Dashboard, and Opportunity Detail's Link-Quote modal — not Track Shipments."""
    pages = {s for s, tools in PAGE_TOOLS.items() if "list_quotes" in tools}
    assert pages == {"dashboard", "", "opportunities"}


@pytest.mark.parametrize("slug", ["setallocation", "orderprocess", "arprocess",
                                  "intransitreturns"])
def test_the_quote_workflow_pages_can_read_a_quotes_status(slug) -> None:
    """Each of these screens loads the quote header; "what's the status of quote X"
    went unanswered there, or was answered from an empty endpoint instead."""
    assert "get_quote_header" in PAGE_TOOLS[slug]


def test_the_quote_header_is_create_quotes_not_the_dashboards() -> None:
    assert "get_quote_header" not in PAGE_TOOLS["dashboard"]
    assert "get_quote_header" in PAGE_TOOLS["createquote"]


def test_metrics_reports_belong_to_metrics() -> None:
    assert "get_quotes_by_status" not in PAGE_TOOLS["dashboard"]
    assert "get_quotes_by_status" in PAGE_TOOLS["metrics"]


def test_the_task_list_is_on_opportunities_too() -> None:
    """Its Linked-tasks panel calls loadTasks. Without it, a tasks question asked
    there fanned out into one checklist call per opportunity."""
    assert "list_tasks" in PAGE_TOOLS["opportunities"]
