"""FastMCP app: registers Globus One tools over streamable-HTTP.

Run:  python -m globus_mcp_server.server   (or: globus-mcp)

Registered tools mirror the free functions in ``tools/`` plus two scoping tools
(NEU-978): ``get_tools_for_page`` and ``get_tool_page_mapping`` — the server is
the source of truth for which tools apply to which portal page.
"""

from __future__ import annotations

import functools
import inspect
from typing import Annotated, Any

from pydantic import Field

from mcp.server.fastmcp import FastMCP

from . import empty_results, scoping
from .auth import build_auth_strategy
from .client import GlobusClient
from .codes import CodeResolver
from .config import settings
from .tools import Runtime
from .tools import allocation as allocation_tools
from .tools import analysis as analysis_tools
from .tools import context as ctx_tools
from .tools import crm as crm_tools
from .tools import fulfillment as fulfillment_tools
from .tools import governance as governance_tools
from .tools import metrics as metrics_tools
from .tools import opportunities as opp_tools
from .tools import quotes as quote_tools
from .tools import reference as reference_tools
from .tools import templates as template_tools

mcp = FastMCP("globus-one", host=settings.host, port=settings.port)

def _request_call_ctx() -> dict[str, Any]:
    """The acting user's token, read off the inbound HTTP request.

    Corporate GPT forwards the end user's JWT as ``Authorization: Bearer <jwt>`` on
    the MCP connection (see ``make_globus_toolset``). Nothing here used to read it,
    so it never reached :class:`PassthroughAuth` — see ``Runtime.context``.

    Never raises. Returns ``{}`` when called outside a request, when the header is
    absent, or when it is not a usable Bearer token. That is the safe direction:
    ``PassthroughAuth`` treats an empty context as an empty Identity and returns no
    data, rather than falling back to someone else's.
    """
    try:
        request = mcp.get_context().request_context.request
        header = request.headers.get("authorization") or ""
    except Exception:  # noqa: BLE001 — no active request, or a transport without one
        return {}
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return {}
    token = token.strip()
    return {"token": token} if token else {}


_client = GlobusClient()
_rt = Runtime(client=_client, codes=CodeResolver(_client), auth=build_auth_strategy(),
              call_ctx_provider=_request_call_ctx)


def _one_payload(result: Any) -> Any:
    """A non-empty list, wrapped as one object: {"total": n, "results": [...]}.

    FastMCP turns a list result into one content block PER ITEM. The backend reads
    the first block as the result, so it saw one row, built no download and
    trimmed nothing — while the model was handed every block. Release For Sale's
    1,032 rows (~43k tokens) overflowed the 64k context and the turn crashed.
    Wrapped, a list arrives as one block, is captured whole, and is previewed to
    the model as 12 rows. An empty list is left alone: it is the "nothing" the
    empty-result checks look for.
    """
    if isinstance(result, list) and result:
        return {"total": len(result), "results": result}
    return result


def _register_module(module) -> None:
    """Register every public async tool function in ``module`` as an MCP tool.

    Each tool function has the shape ``async def name(rt, <typed args>, call_ctx=None)``.
    We expose it to MCP with ``rt``/``call_ctx`` stripped from the signature (they
    are injected here), preserving the typed params + docstring for the schema.
    """
    for name in dir(module):
        if name.startswith("_"):
            continue
        fn = getattr(module, name)
        if not (inspect.iscoroutinefunction(fn) and getattr(fn, "__module__", "") == module.__name__):
            continue
        sig = inspect.signature(fn)
        public_params = [p for n, p in sig.parameters.items() if n not in ("rt", "call_ctx")]

        def _make(fn):
            public_sig = sig.replace(parameters=public_params)
            about_one_quote = "quote_id" in public_sig.parameters

            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                result = await fn(_rt, *args, **kwargs)
                if about_one_quote:
                    quote_id = public_sig.bind_partial(*args, **kwargs).arguments.get("quote_id", "")
                    result = await empty_results.explain_empty(
                        fn.__name__, str(quote_id or ""), result,
                        lambda q: quote_tools.get_quote_header(_rt, q))
                return _one_payload(result)
            wrapper.__signature__ = public_sig
            wrapper.__annotations__ = {k: v for k, v in getattr(fn, "__annotations__", {}).items()
                                       if k not in ("rt", "call_ctx")}
            return wrapper

        mcp.tool()(_make(fn))


# ---- scoping (NEU-978) --------------------------------------------------
@mcp.tool()
def get_tools_for_page(page_uri: str) -> dict[str, Any]:
    """Return the tool names the assistant may use on the given portal page.

    page_uri is the current portal URL/route, e.g. '/quote/#/opportunities'.
    """
    return {"page": scoping.page_slug(page_uri), "tools": scoping.tools_for_page(page_uri)}


@mcp.tool()
def get_tool_page_mapping() -> dict[str, list[str]]:
    """Full page→tools mapping (server-owned source of truth)."""
    return scoping.mapping()


# ---- context tools ------------------------------------------------------
@mcp.tool()
async def get_user_context(login: str = "") -> dict[str, Any]:
    """The signed-in user's ids and access level. Leave `login` empty for yourself."""
    return await ctx_tools.get_user_context(_rt, login)


@mcp.tool()
async def list_companies(party_id: str = "", company_ids: str = "") -> Any:
    """Companies (entities) visible to a party — yours if `party_id` is left empty."""
    return _one_payload(await ctx_tools.list_companies(_rt, party_id, company_ids))


@mcp.tool()
async def lookup_codes(code_grp: str) -> Any:
    """Return id→label entries for a Globus One code group (CRMSTG stages, CRMPRD products, CRMDCF deal confidence, CRMENT entity types)."""
    return _one_payload(await ctx_tools.lookup_codes(_rt, code_grp))


# ---- opportunity tools --------------------------------------------------
# What each opportunity filter MEANS, for the model choosing one. Values are not
# listed here: get_filter_options returns the ones that exist, from the data.
_VALUES = " Valid values: get_filter_options. Several may be comma-separated."


@mcp.tool()
async def list_opportunities(
    deal_confidence: Annotated[str, Field(description="How confident the rep is in the deal, as a colour." + _VALUES)] = "",
    rsm: Annotated[str, Field(description="The sales rep who owns the deal (name or id)." + _VALUES)] = "",
    zone_director: Annotated[str, Field(description="The zone director over the deal (name or id)." + _VALUES)] = "",
    area_director: Annotated[str, Field(description="The area director over the deal (name or id)." + _VALUES)] = "",
    stage: Annotated[str, Field(description="Where the deal is in the sales process, including the final stage a finished deal reaches." + _VALUES)] = "",
    status: Annotated[str, Field(description="Whether the deal is still open or how it ended." + _VALUES)] = "",
    product_interests: Annotated[str, Field(description="Products the customer is interested in." + _VALUES)] = "",
    account_name: Annotated[str, Field(description="Customer account name; a part of it matches.")] = "",
    expected_close_from: Annotated[str, Field(description="Expected close date, from (MM/DD/YYYY).")] = "",
    expected_close_to: Annotated[str, Field(description="Expected close date, to (MM/DD/YYYY).")] = "",
    created_from: Annotated[str, Field(description="Created date, from (MM/DD/YYYY).")] = "",
    created_to: Annotated[str, Field(description="Created date, to (MM/DD/YYYY).")] = "",
    value_min: Annotated[str, Field(description="Minimum deal value (a number).")] = "",
    value_max: Annotated[str, Field(description="Maximum deal value (a number).")] = "",
    opp_name: Annotated[str, Field(description="Opportunity name; a part of it matches.")] = "",
    quarters: Annotated[str, Field(description='Close quarter(s) of the current year, like "Q1,Q3".')] = "",
    company: Annotated[str, Field(description="Only these of your companies (name or id; several may be comma-separated, see list_companies). Leave empty for all your companies.")] = "",
    page: int = 1, page_size: int = 50,
    sort_field: Annotated[str, Field(description="value, probability, expectedCloseDate, createdDate, opportunityName, nextFollowUp, accountName, stage or dealConfidence.")] = "dealConfidence",
    sort_dir: Annotated[str, Field(description="asc or desc.")] = "asc",
) -> dict[str, Any]:
    """Search opportunities by any combination of filters, in one call. Each word in
    a question belongs to one filter — if you are not sure which, or of the exact
    value, call get_filter_options first: it lists every valid value per filter."""
    return await opp_tools.list_opportunities(
        _rt, deal_confidence=deal_confidence, rsm=rsm, zone_director=zone_director,
        area_director=area_director, stage=stage, status=status,
        product_interests=product_interests, account_name=account_name,
        expected_close_from=expected_close_from, expected_close_to=expected_close_to,
        created_from=created_from, created_to=created_to,
        value_min=value_min, value_max=value_max, opp_name=opp_name, quarters=quarters,
        company=company, page=page, page_size=page_size, sort_field=sort_field, sort_dir=sort_dir)


@mcp.tool()
async def get_opportunity(opp_id: int) -> dict[str, Any]:
    """Full detail for one opportunity by id."""
    return await opp_tools.get_opportunity(_rt, opp_id)


@mcp.tool()
async def get_opportunity_stats(expected_close_from: str = "",
                                expected_close_to: str = "") -> dict[str, Any]:
    """Pipeline figures: called / called-value / chasing-for-quarter / closed."""
    return await opp_tools.get_opportunity_stats(_rt, expected_close_from, expected_close_to)


@mcp.tool()
async def get_filter_options() -> dict[str, Any]:
    """The valid value for every opportunity filter — people, stages, statuses,
    deal confidences and product interests — taken from your pipeline."""
    return await opp_tools.get_filter_options(_rt)


@mcp.tool()
async def get_opportunity_tasks(opp_id: int) -> Any:
    """Checklist tasks on an opportunity — each task, its owner, due date and status."""
    return _one_payload(await opp_tools.get_opportunity_tasks(_rt, opp_id))


@mcp.tool()
async def get_task_notes(task_id: int) -> Any:
    """Notes on one checklist task, by task id (from get_opportunity_tasks)."""
    return _one_payload(await opp_tools.get_task_notes(_rt, task_id))


@mcp.tool()
async def get_opportunity_products(opp_id: int) -> Any:
    """Product-interest lines on an opportunity."""
    return _one_payload(await opp_tools.get_opportunity_products(_rt, opp_id))


@mcp.tool()
async def get_opportunity_quotes(opp_id: int) -> Any:
    """Quotes linked to an opportunity."""
    return _one_payload(await opp_tools.get_opportunity_quotes(_rt, opp_id))


@mcp.tool()
async def get_opportunity_activities(opp_id: int) -> Any:
    """Activity timeline for an opportunity."""
    return _one_payload(await opp_tools.get_opportunity_activities(_rt, opp_id))


@mcp.tool()
async def get_opportunity_contacts(opp_id: int) -> Any:
    """Contacts / champions on an opportunity."""
    return _one_payload(await opp_tools.get_opportunity_contacts(_rt, opp_id))


@mcp.tool()
async def get_opportunity_notes(opp_id: int) -> Any:
    """Free-text notes on an opportunity."""
    return _one_payload(await opp_tools.get_opportunity_notes(_rt, opp_id))


@mcp.tool()
async def get_opportunity_history(opp_id: int, page: int = 1, page_size: int = 10) -> Any:
    """Field-level change history for an opportunity."""
    return _one_payload(await opp_tools.get_opportunity_history(_rt, opp_id, page, page_size))


# ---- remaining portal domains (auto-registered) ------------------------
# quotes, templates, metrics, fulfillment, allocation, reference, governance —
# every public async read tool in these modules is exposed as an MCP tool.
for _mod in (quote_tools, template_tools, metrics_tools, fulfillment_tools,
             allocation_tools, reference_tools, governance_tools, analysis_tools,
             crm_tools):
    _register_module(_mod)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
