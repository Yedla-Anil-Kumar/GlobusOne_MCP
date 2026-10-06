"""Robot-quote tools: dashboard list, header, line items, discounts, a-la-carte.

Covers GmRobotQuoteDashboardController and GmCreateRobotQuoteReportController
read endpoints. Thin wrappers over the shared client (identity params are merged
in automatically; the envelope is unwrapped to clean data).
"""

from __future__ import annotations

import datetime as _dt
import difflib
from typing import Any, Mapping, Optional

from ..companies import company_scope, unmatched_company_result
from ..empty_results import quote_not_found
from . import PORTAL_DATE_FORMAT, Runtime, header_body
from .opportunities import _match_code, _match_person, _normalise_date

# Key fields surfaced from each quote-dashboard row (trimmed from a wide record).
_QUOTE_ROW = {
    "strQuoteId": "quoteId", "strRobotName": "robot", "strCustomerName": "customer",
    "strCompanyNm": "company", "strCurrency": "currency",
    "strQuoteStartDate": "startDate", "strQuoteExpDate": "expiryDate",
    # The NAME, not `strQuoteStatus`, which the dashboard always returns null.
    # Mapping that field handed the model a thousand quotes with no status on
    # any of them, and it answered "0 Approved and 0 Draft" holding all of them.
    "strQuoteTotal": "total", "strQuoteStatusNm": "status",
    "strQuoteStatusId": "statusId",
    "strCapitalAccName": "capitalAccount", "strMetalAccName": "metalAccount",
    "strCapitalSalesrepId": "salesRepId",
    # The grid's Sales Rep, RSM and ZD columns.
    "strSalesRepNm": "salesRep", "strRsmNm": "rsm", "strZdRsm": "zoneDirector",
}

# Person filters the dashboard only takes as user ids (strCapitalAdId /
# strCapitalVpId), against the row field that carries the name. The DAO puts the
# value into a numeric comparison, so "Austin Ragans" sent there made the query
# fail; a name is matched against the rows instead, as the grid's column filter does.
_PERSON_FILTERS = {"rsm": ("strCapitalAdId", "strRsmNm"),
                   "zone_director": ("strCapitalVpId", "strZdRsm")}


# Stands in for an open start date: earlier than any quote in the portal.
_OPEN_START = "01/01/2000"


def _trim_quote(r: dict[str, Any]) -> dict[str, Any]:
    return {out: r.get(src) for src, out in _QUOTE_ROW.items()}


async def list_quotes(
    rt: Runtime,
    quote_id: str = "", customer_account_id: str = "", customer_name: str = "",
    status: str = "",
    rsm: str = "", zone_director: str = "",
    start_date: str = "", end_date: str = "",
    expiry_from: str = "", expiry_to: str = "",
    company: str = "",
    call_ctx: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """List robot quotes for the quote dashboard, filterable by customer (account id,
    or a name fragment), RSM, zone director, status, quote date and expiry date.

    `status` is one or more status NAMES as the dashboard shows them, comma-separated
    ("Approved,Draft"); dates are MM/DD/YYYY (ISO is converted). rsm and
    zone_director are the person's name as the grid shows it (or user id).
    company is one or more of your companies (name or id, see list_companies);
    empty means your default company, as the dashboard opens on.
    """
    auth = await rt.context(call_ctx)
    scope = await company_scope(rt, auth, company, one_by_default=True)
    if scope.unmatched:
        return unmatched_company_result(scope, {"total": 0, "quotes": []})
    people = {arg: str(value or "").strip()
              for arg, value in (("rsm", rsm), ("zone_director", zone_director))}
    # The dashboard speaks MM/DD/YYYY and answers anything else with zero rows —
    # the same silent mismatch the opportunity filters had. A model reaches for
    # ISO unprompted; converting up front costs nothing and saves a wasted call.
    unmatched: dict[str, Any] = {}
    dates = {}
    for arg, value in (("start_date", start_date), ("end_date", end_date)):
        if str(value or "").strip():
            normalised = _normalise_date(value)
            if normalised is None:
                unmatched[arg] = {"requested": value, "expectedFormat": "MM/DD/YYYY"}
            dates[arg] = normalised or value
        else:
            dates[arg] = value
    # The dashboard always sends BOTH ends of its range. "Since July 1" arrived as
    # a start with an empty end and matched nothing; an open end is closed at
    # today, an open start at a date before any quote.
    if not unmatched:
        if dates["start_date"] and not dates["end_date"]:
            dates["end_date"] = _dt.date.today().strftime("%m/%d/%Y")
        elif dates["end_date"] and not dates["start_date"]:
            dates["start_date"] = _OPEN_START

    # Exactly the filters the dashboard's own body carries. Sales rep, metal AD/VP
    # and robot name were offered too, but the dashboard never reads them: a model
    # filtering by rep got every quote back and reported them as that rep's.
    # (The dashboard sends the RSM as strCapitalAdId and the ZD as strCapitalVpId.)
    body = {
        "strQuoteId": quote_id, "strCustomerId": customer_account_id,
        "strCustomerName": customer_name,
        "strQuoteStatus": status,
        "strCapitalAdId": people["rsm"] if people["rsm"].isdigit() else "",
        "strCapitalVpId": (people["zone_director"]
                           if people["zone_director"].isdigit() else ""),
        "strQuoteExpFromDate": _normalise_date(expiry_from) or expiry_from,
        "strQuoteExpToDate": _normalise_date(expiry_to) or expiry_to,
        # The dashboard reads its date range from strQuoteFromDate/strQuoteToDate
        # (its current code, GmRobotQuoteDashboardController). These were sent as
        # strQuoteStartDate/strQuoteExpDate — names it ignores — so every date
        # filter was silently dropped and "since July 1" meant "ever".
        "strQuoteFromDate": dates["start_date"], "strQuoteToDate": dates["end_date"],
        "strDtFormat": PORTAL_DATE_FORMAT,
    }
    # The DAO filters strQuoteStatus as an IN list of numeric status IDS. Names
    # are resolved BEFORE the query: sent as they were, "Approved,Draft" went into
    # that IN list and the dashboard answered with an error ("the Quote Dashboard
    # is experiencing an error"). Rows carry both name and id, so the mapping is
    # learned from the data, not maintained here. Comma-separated, several allowed.
    wanted = [s.strip() for s in str(status or "").split(",") if s.strip()]
    matched_as: dict[str, str] = {}
    if wanted and not all(s.isdigit() for s in wanted):
        vocabulary = await _quote_status_ids(rt, auth)
        ids, unknown = [], []
        for name in wanted:
            resolved = name if name.isdigit() else _match_code(name, list(vocabulary))
            if resolved is None:
                resolved = _one_status_meant(name, list(vocabulary))
                if resolved is not None:
                    matched_as[name] = resolved
            if resolved is None:
                unknown.append(name)
            else:
                ids.append(resolved if resolved.isdigit() else vocabulary[resolved])
        if unknown and vocabulary:
            unmatched["status"] = {"requested": ", ".join(unknown),
                                   "validValues": sorted(vocabulary)}
        if not ids:
            result = {"total": 0, "quotes": []}
            if unmatched:
                result["unmatchedFilters"] = unmatched
                result["caveat"] = " ".join(_quote_caveats(unmatched))
            return result
        body["strQuoteStatus"] = ",".join(ids)
    # The dashboard filters one company at a time (company_id = ?).
    rows = []
    for cid in scope.id_list or [None]:
        rows.extend(await _quote_rows(
            rt, body if cid is None else {**body, "strCompanyId": cid}, auth))
    for arg, value in people.items():
        if value and not value.isdigit():
            rows, miss = _rows_for_person(rows, value, _PERSON_FILTERS[arg][1])
            if miss:
                unmatched[arg] = miss

    result = {"total": len(rows), "quotes": [_trim_quote(r) for r in rows]}
    if matched_as:
        result["statusMatchedAs"] = matched_as
        result["note"] = "; ".join(
            f"no status is called {asked!r} — read as {used!r}; say so in the answer"
            for asked, used in matched_as.items())
    if unmatched and not rows:
        result["unmatchedFilters"] = unmatched
        result["caveat"] = " ".join(_quote_caveats(unmatched))
    return result


def _one_status_meant(name: str, statuses: list[str]) -> Optional[str]:
    """The one real status a near-miss name plainly means — "Quote Stage" for
    "Quote Staged" — or None when it could mean several or none. The statuses
    are the ones the quotes carry, so nothing here is a fixed list."""
    target = " ".join(name.split()).casefold()
    if not target:
        return None
    by_key = {" ".join(s.split()).casefold(): s for s in statuses}
    contained = [s for key, s in by_key.items() if target in key or key in target]
    if len(contained) == 1:
        return contained[0]
    if contained:
        return None                        # "Pending" is two statuses; do not pick one
    close = difflib.get_close_matches(target, list(by_key), n=2, cutoff=0.85)
    return by_key[close[0]] if len(close) == 1 else None


def _rows_for_person(rows: list, name: str, field: str) -> tuple[list, Optional[dict]]:
    """Rows whose `field` holds `name` — contained, as the grid's column filter
    matches — or, failing that, the one name it plainly means ("Austin Ragans
    (US)"). Returns the rows and, when none match, what to report."""
    target = " ".join(name.split()).casefold()
    def text(row: Any) -> str:
        return " ".join(str(row.get(field) or "").split()) if isinstance(row, dict) else ""
    hits = [r for r in rows if target in text(r).casefold()]
    if hits:
        return hits, None
    names = sorted({text(r) for r in rows} - {""})
    resolved = _match_person(name, names)
    if resolved:
        return [r for r in rows if text(r) == resolved], None
    return [], {"requested": name,
                "closestValues": difflib.get_close_matches(name, names, n=5, cutoff=0.4)}


def _quote_caveats(unmatched: dict[str, Any]) -> list[str]:
    """What an empty result can honestly be said to mean, per filter.

    The two cases differ. A date that could not be read says nothing about the
    quotes — "none" would be false. A status name no quote carries is learned
    from the quotes themselves, so it cannot tell a typo from a real status that
    currently has no quotes; either way "no quote has that status" is TRUE, and
    it is the only thing that can be said.
    """
    notes = []
    for arg in ("start_date", "end_date"):
        if arg in unmatched:
            notes.append(
                f"The {arg.replace('_', ' ')} {unmatched[arg]['requested']!r} could "
                "not be read — the dashboard expects MM/DD/YYYY. Say so, and do NOT "
                "report this as there being no quotes."
            )
    for arg in _PERSON_FILTERS:
        if arg in unmatched:
            close = ", ".join(unmatched[arg]["closestValues"]) or "none close"
            notes.append(
                f"No quote in this selection has {arg.replace('_', ' ')} "
                f"{unmatched[arg]['requested']!r} (closest names on these quotes: "
                f"{close}). Say that — do not claim the person does not exist."
            )
    if "status" in unmatched:
        present = ", ".join(unmatched["status"]["validValues"])
        notes.append(
            f"No quote currently has the status {unmatched['status']['requested']!r}. "
            f"The statuses quotes actually carry are: {present}. Say that, and "
            "offer those — do not claim the status does not exist."
        )
    return notes


async def _quote_rows(rt: Runtime, body: dict[str, Any], auth: Any) -> list:
    rows = await rt.client.post("loadQuoteDashboardDtls", body, auth=auth)
    return rows if isinstance(rows, list) else []


async def _quote_status_ids(rt: Runtime, auth: Any) -> dict[str, str]:
    """Status name → id, from the quotes themselves.

    Unfiltered on purpose: a date or rep filter would hide statuses that exist
    but have no quotes in that slice, and those would then read as unknown.
    """
    try:
        rows = await _quote_rows(rt, {}, auth)
    except Exception:  # noqa: BLE001 — a diagnosis must not break the query
        return {}
    pairs: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("strQuoteStatusNm") or "").strip()
        code = str(row.get("strQuoteStatusId") or "").strip()
        if name and code:
            pairs.setdefault(name, code)
    return pairs


async def check_robot_name_exists(rt: Runtime, robot_name: str, company_id: str = "",
                                  call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Check whether a robot name already exists (validation)."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(
        f"checkRobotNameExists?robotName={robot_name}&companyId={company_id}", auth=auth)


async def get_robot_voltage(rt: Runtime, company_id: str = "",
                            call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """The robots (robotMasterId, robotName) for a company — yours if left empty,
    as the Release For Sale screen's Robot dropdown loads them."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "loadRobotVoltageDtls", {"companyId": company_id or auth.identity.company_id}, auth=auth)


async def get_quote_header(rt: Runtime, quote_id: str,
                           call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Quote header: customer, rep, totals, the quote's current status, and who
    confirmed it and when — the answer to "what is the status of quote X" and to
    whether its set allocation has been confirmed."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("loadQuoteHeaderDetails", header_body(quote_id), auth=auth)


async def get_quote_header_history(rt: Runtime, quote_id: str,
                                   call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Historical/versioned quote header details for a quote id."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "loadQuoteHeaderDetailsHistory",
        {"strQuoteId": quote_id, "strDtFormat": PORTAL_DATE_FORMAT}, auth=auth)


async def get_quote_line_items(rt: Runtime, quote_id: str, category_id: str = "",
                               robot_master_id: str = "",
                               call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Line items (parts/sets, price, qty) for a quote, optionally by category."""
    auth = await rt.context(call_ctx)
    # The screen sends the quote as strQuoteId; this sent it as strRefId.
    return await rt.client.post(
        "loadQuoteCategoryDetails",
        {"strQuoteId": quote_id, "strCategoryId": category_id,
         "robotMasterId": robot_master_id},
        auth=auth)


async def get_quote_category_total(rt: Runtime, quote_id: str, category_id: str = "",
                                   robot_master_id: str = "",
                                   call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Subtotal/total figures for a quote category."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "fetchQuoteCategoryTotal",
        {"strQuoteId": quote_id, "strCategoryId": category_id, "robotMasterId": robot_master_id},
        auth=auth)


async def get_discount_approval(rt: Runtime, quote_id: str,
                                call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Discount approval detail rows for a quote."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("loadDiscountApprovalDetails", {"strQuoteId": quote_id}, auth=auth)


async def list_robot_categories(rt: Runtime, robot_master_id: str = "",
                                call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Robot category master list for quote build."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("loadRobotCategory", {"robotMasterId": robot_master_id}, auth=auth)


async def list_alacarte_items(rt: Runtime, quote_id: str, category_id: str = "", company_id: str = "",
                              call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """A-la-carte item list for a quote category."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "fetchALcarteItems",
        {"strQuoteId": quote_id, "strCategoryId": category_id, "strCompanyId": company_id}, auth=auth)


async def list_alacarte_durapro_verzera_items(rt: Runtime, quote_id: str, category_id: str = "",
                                              call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """DuraPro/Verzera a-la-carte item list for a quote category."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "fetchALcarteDuraProVerzeraItems",
        {"strQuoteId": quote_id, "strCategoryId": category_id}, auth=auth)


async def get_quote_pdf(rt: Runtime, quote_id: str,
                        call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Render the quote as a PDF (print) for a quote id."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("printQuotePDF", {"strQuoteId": quote_id}, auth=auth)


async def validate_quote_id(rt: Runtime, quote_id: str,
                            call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Whether a quote id exists and is visible to you."""
    auth = await rt.context(call_ctx)
    count = await rt.client.post("validateQuoteId", {"strQuoteId": quote_id}, auth=auth)
    # The endpoint answers with a match COUNT (intResult), and 0 means "no such
    # quote" — a number, not the empty result the not-found check looks for, so a
    # missing quote came back as a bare 0 for the model to interpret.
    try:
        matches = int(count)
    except (TypeError, ValueError):
        return count
    if matches == 0:
        return quote_not_found(quote_id)
    return {"quoteId": quote_id, "found": True, "matches": matches}


async def get_quote_company_id(rt: Runtime, quote_id: str,
                               call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Company id associated with a quote."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("fetchQuoteCompanyId", {"strQuoteId": quote_id}, auth=auth)
