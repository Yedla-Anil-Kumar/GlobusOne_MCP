"""An unmapped page must not impersonate the opportunities page.

Live failure: asked about opportunities from Tasks, or from the portal's "#/"
landing route, the assistant answered in full — as though the user were looking
at the opportunities list. Page scoping appeared to be doing nothing.

It was doing exactly what it was told. `DEFAULT_TOOLS` was `OPPORTUNITY_TOOLS`,
so every route absent from PAGE_TOOLS silently became the opportunities page —
and the portal's landing route is "#/" with no segment at all, which resolves to
no slug and so was never mapped.

The size reasoning behind a narrow default still holds: every advertised tool
ships its JSON schema on every model request, and a wide fallback caused upstream
500s. The CHOICE of which narrow set was the bug. Scoping that quietly stops
scoping is worse than none, because nothing reveals the gap.

Run:  PYTHONPATH=. pytest tests/test_page_scoping_default.py -q
"""

import logging

import pytest

from globus_mcp_server.scoping import (
    CONTEXT_TOOLS,
    DEFAULT_TOOLS,
    OPPORTUNITY_TOOLS,
    PAGE_TOOLS,
    page_slug,
    tools_for_page,
)

PORTAL = "https://erptest3.globusone.com/quote/#"


def _tools(route: str) -> list[str]:
    return tools_for_page(f"{PORTAL}{route}")


# ── the live failure ──────────────────────────────────────────────────────

def test_the_landing_route_is_not_the_opportunities_page() -> None:
    """"#/" renders the Dashboard and resolves to no slug at all."""
    assert page_slug(f"{PORTAL}/") == ""
    assert "list_opportunities" not in _tools("/")


def test_the_landing_route_gets_what_the_dashboard_gets() -> None:
    assert set(_tools("/")) == set(_tools("/dashboard"))


def test_an_unmapped_route_offers_lookups_only() -> None:
    assert set(_tools("/somethingnobodymapped")) == set(CONTEXT_TOOLS)


def test_an_unmapped_route_cannot_answer_about_opportunities() -> None:
    for tool in OPPORTUNITY_TOOLS:
        assert tool not in _tools("/somethingnobodymapped"), \
            f"{tool} reachable from an unmapped page"


def test_the_default_is_no_longer_the_opportunity_toolset() -> None:
    """The whole bug, in one line."""
    assert DEFAULT_TOOLS != OPPORTUNITY_TOOLS
    assert not DEFAULT_TOOLS


# ── the pages that should still work ──────────────────────────────────────

def test_the_opportunities_page_is_unaffected() -> None:
    tools = _tools("/opportunities")
    assert "list_opportunities" in tools
    assert "get_pipeline_analysis" in tools


@pytest.mark.parametrize("slug", sorted(s for s in PAGE_TOOLS if s))
def test_every_mapped_page_still_offers_its_own_tools(slug) -> None:
    """Narrowing the default must not have narrowed anything that was mapped."""
    tools = set(_tools(f"/{slug}"))
    assert set(PAGE_TOOLS[slug]) <= tools
    assert set(CONTEXT_TOOLS) <= tools, "lookups are available everywhere"


def test_a_parameterised_route_still_resolves() -> None:
    """#/createquote/Q-1 is the createquote page."""
    assert "get_quote_header" in _tools("/createquote/Q-1")


# ── the gap is visible ────────────────────────────────────────────────────

def test_an_unmapped_page_says_so_in_the_log(caplog) -> None:
    """The only way anyone learns a route is missing."""
    with caplog.at_level(logging.WARNING, logger="globus_mcp_server.scoping"):
        _tools("/somethingnobodymapped")

    assert any("somethingnobodymapped" in r.message for r in caplog.records), \
        "an unmapped route must name itself, or the gap stays invisible"


def test_a_mapped_page_is_quiet(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="globus_mcp_server.scoping"):
        _tools("/opportunities")

    assert not caplog.records


# ── the portal's real routes ──────────────────────────────────────────────

# Read from the live portal's address bar, one per sidebar item. Guessed slugs
# are how a page ends up either unscoped or silently useless.
PORTAL_ROUTES = (
    "opportunities", "tasks", "dashboard", "setallocation",
    "orderprocess", "releaseforsale", "shippingdetail", "intransitreturns",
)


@pytest.mark.parametrize("slug", PORTAL_ROUTES)
def test_every_portal_route_is_mapped(slug) -> None:
    """An unmapped route silently loses its tools now that the default is empty."""
    assert slug in PAGE_TOOLS, (
        f"{slug!r} is a real page in the portal's sidebar and has no entry"
    )


@pytest.mark.parametrize("slug", PORTAL_ROUTES)
def test_no_portal_route_logs_the_unmapped_warning(slug, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="globus_mcp_server.scoping"):
        _tools(f"/{slug}")

    assert not caplog.records, f"{slug!r} is still falling through to the default"


def test_tasks_offers_the_task_tools_and_not_the_pipeline() -> None:
    """Deliberately changed: Tasks was mapped to nothing because the server had no
    task tool. It now has the two the Tasks screen calls, and still none of the
    opportunity list's."""
    tools = set(_tools("/tasks"))
    assert {"get_opportunity_tasks", "get_task_notes"} <= tools
    assert "list_opportunities" not in tools
