"""Fulfillment read tools: release-for-sale, orders, INR/AR, shipping, in-transit returns.

Covers GmReleaseForSaleController, GmOrderProcessingController,
GmInrProcessingController, GmShippingInfoController, GmShippingHistoryController,
GmInTransitReturnsController (read endpoints only).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from . import Runtime, header_body, header_row
from ..client import GlobusApiError
from ..empty_results import header_status, is_empty

# The Order Process screen's two detail grids, loaded by strAccountType.
ACCOUNT_TYPES = ("Capital", "Metal")


# -- release for sale -----------------------------------------------------
# The Release For Sale screen (GmReleaseForSaleController) is not per quote: it
# loads by a Robot (loadRobotVoltageDtls for the user's company → robotMasterId /
# robotName) OR a quote, in a Country (code group RFSALE → codeId / codeNm), and
# the country is required. Names are resolved through those same two lists.
RFS_COUNTRY_CODE_GROUP = "RFSALE"


def _one_named(requested: str, options: dict[str, str]) -> Optional[str]:
    """The id whose name `requested` means: exact (any case), else the one name
    containing it. None when it matches none, or several."""
    target = " ".join(str(requested).split()).casefold()
    exact = [i for i, n in options.items() if " ".join(n.split()).casefold() == target]
    if len(exact) == 1:
        return exact[0]
    partial = [i for i, n in options.items() if target and target in n.casefold()]
    return partial[0] if len(partial) == 1 else None


async def _rfs_robots(rt: Runtime, auth: Any) -> dict[str, str]:
    rows = await rt.client.post(
        "loadRobotVoltageDtls", {"companyId": auth.identity.company_id}, auth=auth)
    return {str(r["robotMasterId"]): str(r.get("robotName") or r["robotMasterId"])
            for r in (rows if isinstance(rows, list) else [])
            if isinstance(r, dict) and r.get("robotMasterId") not in (None, "")}


async def _rfs_countries(rt: Runtime, auth: Any) -> dict[str, str]:
    rows = await rt.client.post("loadCodeLookup", {"codeGrp": RFS_COUNTRY_CODE_GROUP}, auth=auth)
    return {str(r["codeId"]): str(r.get("codeNm") or r["codeId"])
            for r in (rows if isinstance(rows, list) else [])
            if isinstance(r, dict) and r.get("codeId") not in (None, "")}


async def get_release_for_sale(rt: Runtime, quote_id: str = "", robot: str = "",
                               country: str = "", release_for_sale: str = "",
                               section: str = "",
                               call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Release For Sale parts for a robot OR a quote, in a country — each part's
    section (its category, e.g. "Capital E3D"), set, part number, name and rfs
    (Y = released for sale).

    robot and country are the names the page's dropdowns show ("EXCELSIUS GPS AND
    HUB, 120V", "EU") or their ids; the country is required. release_for_sale
    "Y" or "N" keeps only the parts with that flag; section keeps one section."""
    auth = await rt.context(call_ctx)
    unmatched: dict[str, Any] = {}

    country_id = str(country or "").strip()
    countries: dict[str, str] = {}
    if not country_id.isdigit():
        countries = await _rfs_countries(rt, auth)
        resolved = _one_named(country_id, countries) if country_id else None
        if resolved is None:
            unmatched["country"] = {"requested": country or None,
                                    "validValues": sorted(countries.values())}
        country_id = resolved or ""

    robot_id = str(robot or "").strip()
    robots: dict[str, str] = {}
    if robot_id and not robot_id.isdigit():
        robots = await _rfs_robots(rt, auth)
        resolved = _one_named(robot_id, robots)
        if resolved is None:
            unmatched["robot"] = {"requested": robot, "validValues": sorted(robots.values())}
        robot_id = resolved or ""
    if not robot_id and not str(quote_id or "").strip() and "robot" not in unmatched:
        unmatched["robot"] = {"requested": None,
                              "validValues": sorted((robots or await _rfs_robots(rt, auth)).values())}

    flag = str(release_for_sale or "").strip()[:1].upper()
    if flag and flag not in ("Y", "N"):
        unmatched["release_for_sale"] = {"requested": release_for_sale, "validValues": ["Y", "N"]}

    if unmatched:
        # The screen refuses these too ("Please Select Robot / Quote ID And Country").
        return {"total": 0, "parts": [], "unmatchedFilters": unmatched,
                "caveat": ("Nothing was queried: the Release For Sale list needs a robot "
                           "or a quote, and a country, each one of the values listed in "
                           "unmatchedFilters. Ask for, or use, one of those — do not "
                           "look through quotes one by one.")}

    rows = await rt.client.post(
        "loadReleaseForSaleDtls",
        {"strQuoteId": quote_id, "strRobotMasterId": robot_id, "strCountryId": country_id},
        auth=auth)
    wanted_section = str(section or "").strip()
    if not flag and not wanted_section:
        return rows
    parts = [r for r in (rows if isinstance(rows, list) else []) if isinstance(r, dict)]
    kept = parts
    if wanted_section:
        # Resolved against the sections these parts carry — the list differs by robot.
        present = {str(r.get("section") or ""): str(r.get("section") or "")
                   for r in parts if r.get("section")}
        matched = _one_named(wanted_section, present)
        if matched is None:
            return {"total": 0, "parts": [], "ofParts": len(parts),
                    "unmatchedFilters": {"section": {"requested": section,
                                                     "validValues": sorted(present)}},
                    "caveat": (f"No section here is called {section!r}. The sections these "
                               "parts have are listed in unmatchedFilters — offer those.")}
        kept = [r for r in kept if str(r.get("section") or "") == matched]
        wanted_section = matched
    if flag:
        kept = [r for r in kept if str(r.get("rfs") or "").upper() == flag]
    return {"total": len(kept), "releaseForSale": flag or None, "section": wanted_section or None,
            "robot": robots.get(robot_id, robot_id) or None,
            "country": countries.get(country_id, country_id) or None,
            "ofParts": len(parts), "parts": kept}


# -- order processing -----------------------------------------------------
async def get_categories_by_account_type(rt: Runtime, quote_id: str, account_type: str = "",
                                         call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """A quote's Capital and Metal Order Detail lines on the Order Process screen —
    each line's category, type, id, description, qty, price, order type, plant
    and order status. account_type is Capital or Metal; empty returns both, each
    line marked with its accountType."""
    auth = await rt.context(call_ctx)
    wanted = [t for t in ACCOUNT_TYPES if t.casefold() == account_type.strip().casefold()]
    if account_type.strip() and not wanted:
        return {"unmatchedFilters": {"account_type": {"requested": account_type,
                                                      "validValues": list(ACCOUNT_TYPES)}},
                "caveat": f"{account_type!r} is not an account type here; use "
                          f"{' or '.join(ACCOUNT_TYPES)}. Nothing was queried."}
    lines: list = []
    failed: dict[str, str] = {}
    for kind in wanted or ACCOUNT_TYPES:
        # A quote with no lines on one account gets an error for that account
        # ("Error while loading category based account type"). Live, the Metal
        # error discarded GMQT-103122-1's four Capital lines, so each account
        # stands on its own.
        try:
            rows = await rt.client.post(
                "loadCategoryBasedOnAccountType",
                {"strQuoteId": quote_id, "strAccountType": kind}, auth=auth)
        except GlobusApiError as exc:
            failed[kind] = str(exc)
            continue
        lines.extend({**r, "accountType": kind} if isinstance(r, dict) else r
                     for r in (rows if isinstance(rows, list) else []))
    if failed and not lines:
        raise GlobusApiError("; ".join(f"{k}: {v}" for k, v in failed.items()))
    if not failed:
        return lines
    return {"total": len(lines), "lines": lines,
            "note": (f"No {' or '.join(failed)} lines could be loaded — Globus One "
                     "answers with an error when the quote has none on that account. "
                     "The lines here are complete for the other account.")}


async def get_order_summary(rt: Runtime, quote_id: str,
                            call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """The quote's order-process status, its totals, and the orders placed so far.

    Lines not yet ordered are the gap between the quote total and the orders.
    """
    auth = await rt.context(call_ctx)
    # The Order Process screen sends the quote's capital account alongside its id,
    # read from the quote header it has already loaded. This sent the id alone
    # and got nothing back. The caller only knows the quote id, so the header is
    # read here; if that fails the call still goes out, as it did before.
    try:
        header = header_row(await rt.client.post(
            "loadQuoteHeaderDetails", header_body(quote_id), auth=auth))
    except Exception:  # noqa: BLE001 — the header only improves the call
        header = {}
    try:
        orders = await rt.client.post(
            "loadOrderSummary",
            {"strQuoteId": quote_id, "strCapitalAccId": str(header.get("strCapitalAccId") or "")},
            auth=auth)
    except GlobusApiError as exc:
        # loadOrderSummary reads the orders already placed; with none, its stored
        # procedure returns nothing and the controller answers "Error while
        # loading Order Summary". Live, the model then guessed set ids for 40s.
        return {
            "quoteId": quote_id,
            "quoteStatus": header_status(header) or None,
            "quoteTotals": _totals(header),
            "orders": None,
            "note": (f"Globus One could not load an order summary ({exc}); it answers "
                     "this way when no orders have been placed yet. The quote's order "
                     "lines, with each line's order status, come from "
                     "get_categories_by_account_type."),
        }
    if is_empty(orders):
        return orders                      # the server wrapper explains an empty one
    # The orders alone answered "order process status" as "all 4 Shipped" while
    # the portal said "Order Process In-Progress": a $9,840 set had not been
    # ordered yet — exactly the gap between the quote total and the orders. The
    # header, already read above, carries both the status and the total.
    return {
        "quoteId": quote_id,
        "quoteStatus": header_status(header) or None,
        "quoteTotals": _totals(header),
        "orders": orders,
        # Live, the model split the orders into "capital $0 / metal $30,678.25"
        # — but $750 and -$2,090 of them were capital lines. Nothing in an order
        # said which account it was on; the split was invented.
        "note": ("`orders` lists only what has been ordered. If they add up to less "
                 "than the quote total, the rest has not been ordered yet — say so, "
                 "and give quoteStatus as the order-process status. Attribute an "
                 "order to capital or metal only where the order itself says which; "
                 "otherwise give the totals without that split."),
    }


def _totals(header: dict) -> dict:
    """The header's money figures — quote, capital and metal totals."""
    return {k: v for k, v in header.items()
            if "total" in k.lower() and v not in (None, "")}


# -- INR / AR processing --------------------------------------------------
async def get_po_details(rt: Runtime, quote_id: str, account_type: str = "",
                         call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Purchase-order details for a quote."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "loadPODetails", {"strQuoteId": quote_id, "strAccountType": account_type}, auth=auth)


async def get_invoiced_report(rt: Runtime, quote_id: str,
                              call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Invoiced report for a quote."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("loadInvoicedReport", {"strQuoteId": quote_id}, auth=auth)


async def list_sold_categories(rt: Runtime, quote_id: str,
                               call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Sold category list for a quote."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("loadSoldCategoryList", {"strQuoteId": quote_id}, auth=auth)


async def list_quote_orders(rt: Runtime, quote_id: str,
                            call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Orders for a quote."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("fetchQuoteOrders", {"strQuoteId": quote_id}, auth=auth)


async def get_po_comments(rt: Runtime, quote_id: str,
                          call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Purchase-order comments for a quote."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("fetchPOComments", {"strQuoteId": quote_id}, auth=auth)


# -- shipping -------------------------------------------------------------
async def get_shipping_info(rt: Runtime, ref_id: str,
                            call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Shipping info records for a reference (quote/order)."""
    auth = await rt.context(call_ctx)
    # strSource "50180" and strAction "fetch" are fixed on the Order Process
    # screen; the caller only ever knew the reference.
    return await rt.client.post(
        "loadShippingInfo",
        {"strRefId": ref_id, "strSource": "50180", "strAction": "fetch"}, auth=auth)


async def _quote_header(rt: Runtime, quote_id: str, auth: Any) -> dict:
    try:
        return header_row(await rt.client.post(
            "loadQuoteHeaderDetails", header_body(quote_id), auth=auth))
    except Exception:  # noqa: BLE001 — the header only fills in the screen's ids
        return {}


async def list_shipping_carriers(rt: Runtime, quote_id: str, ship_to: str = "",
                                 address_id: str = "",
                                 call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """The preferred shipping carrier for a quote's account."""
    auth = await rt.context(call_ctx)
    # The screen takes the account and rep from the quote header it has loaded.
    header = await _quote_header(rt, quote_id, auth)
    return await rt.client.post(
        "loadShippingCarrier",
        {"strRepAccId": str(header.get("strCapitalSalesrepId") or ""),
         "strAccountId": str(header.get("strCapitalAccId") or ""),
         "strShipTo": ship_to, "strAddressId": address_id}, auth=auth)


async def list_shipping_modes(rt: Runtime, quote_id: str, carrier: str = "",
                             call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Shipping modes available for a carrier on a quote's account."""
    auth = await rt.context(call_ctx)
    header = await _quote_header(rt, quote_id, auth)
    return await rt.client.post(
        "loadShippingMode",
        {"strShipCarr": carrier, "strAccountId": str(header.get("strCapitalAccId") or "")},
        auth=auth)


async def list_addresses(rt: Runtime, ship_to_id: str, action: str = "",
                         call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Addresses for a ship-to."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "fetchAddress",
        {"strShipToId": ship_to_id, "strAction": action, "strInActiveFl": ""}, auth=auth)


async def get_shipping_history(rt: Runtime, quote_id: str,
                               call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Shipping history for a quote."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("loadShippingHistory", {"strQuoteId": quote_id}, auth=auth)


# -- in-transit returns ---------------------------------------------------
async def list_in_transit_return_orders(rt: Runtime, quote_id: str,
                                        call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """In-transit orders on a quote that are eligible for return."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("loadInTransitReturnOrders", {"strQuoteId": quote_id}, auth=auth)
