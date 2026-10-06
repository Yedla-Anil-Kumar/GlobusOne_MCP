"""Opportunity tools — the POC core (NEU-981).

``list_opportunities`` maps friendly args onto Globus One's dashboard filter
body; the rest are per-opportunity reads. Coded fields are resolved to labels
via the shared CodeResolver where the API doesn't already return a name.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Mapping, Optional

from . import Runtime
from ..companies import company_scope, unmatched_company_result
from ..config import settings

# friendly arg -> Globus One dashboard body field
_FILTER_MAP = {
    "deal_confidence": "strDealConfidence",
    "rsm": "strRsm",
    "zone_director": "strZoneDirector",
    "area_director": "strAreaDirector",
    "stage": "strStage",
    "status": "strStatus",
    "product_interests": "strProductInterests",
    "account_name": "strAccountName",
    "expected_close_from": "strExpectedCloseFrom",
    "expected_close_to": "strExpectedCloseTo",
    "created_from": "strCreatedDateFrom",
    "created_to": "strCreatedDateTo",
    "value_min": "strValueMin",
    "value_max": "strValueMax",
    # GmOppDashboardSqlBuilder also filters on these: the opportunity name
    # (partial match) and close quarter (CSV like "Q1,Q3").
    "opp_name": "strOppName",
    "quarters": "strQuarters",
}

# fields surfaced from each dashboard row (trimmed from a much larger record)
#
# NOTE: the dashboard row carries the *ID* in strRsm / strZoneDirector /
# strAreaDirector (usually null or a raw code) and the human-readable *name* in
# the parallel *Nm fields (strRsmNm / strZdNm / strAdNm). We surface the names,
# otherwise the agent gets null owners/directors and answers wrongly. The
# filter body (_FILTER_MAP above) still takes the friendly name on strRsm etc.,
# which the endpoint matches server-side — those are input params, not outputs.
_ROW_FIELDS = {
    "strOppId": "id", "strOppName": "name", "strAccountName": "account",
    "strStage": "stage", "strStatus": "status", "strValue": "value",
    "strProbability": "probability", "strDealConfidence": "dealConfidence",
    "strProductInterests": "productInterests",
    "strRsmNm": "rsm", "strZdNm": "zoneDirector", "strAdNm": "areaDirector",
    "strImplantRep": "implantRep",
    "strQuarter": "quarter", "strExpectedCloseDate": "expectedCloseDate",
    "strLastActivity": "lastActivity",
}


def _row(r: dict[str, Any], companies: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    out = {dst: r.get(src) for src, dst in _ROW_FIELDS.items()}
    if companies is not None:
        # Rows from several companies look alike; the id alone means nothing to a reader.
        cid = str(r.get("strCompanyId") or "")
        out["company"] = companies.get(cid, cid) or None
    return out


# Fields whose values must compare as numbers, not strings — "900000" would
# otherwise sort above "2400000".
_NUMERIC_SORT_FIELDS = frozenset({"value", "probability"})


def _sort_key(field: str):
    """Build a sort key for a trimmed row, tolerant of nulls and blanks.

    Missing/blank values sort last regardless of direction, so a row with no
    value never leads a "top by value" list.
    """
    numeric = field in _NUMERIC_SORT_FIELDS

    def key(row: dict[str, Any]):
        raw = row.get(field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return (1, 0.0 if numeric else "")
        if numeric:
            try:
                return (0, float(str(raw).replace(",", "")))
            except (TypeError, ValueError):
                return (1, 0.0)
        return (0, str(raw).lower())

    return key


def _sorted_rows(rows: list[dict[str, Any]], sort_field: str, sort_dir: str) -> list[dict[str, Any]]:
    """Sort trimmed rows in the tool, so ordering is exact and not left to the caller.

    The dashboard endpoint accepts strSortField/strSortDir but does not reliably
    order the rows it returns, which left "top N by value" answers only roughly
    descending. Sorting here makes the order deterministic: the caller can take
    the first N and trust them.
    """
    if not sort_field or not rows or sort_field not in _ROW_FIELDS.values():
        return rows
    descending = str(sort_dir).lower().startswith("desc")
    ordered = sorted(rows, key=_sort_key(sort_field))
    if descending:
        # Reverse only the present values; blanks (key[0] == 1) stay last.
        present = [r for r in ordered if _sort_key(sort_field)(r)[0] == 0]
        missing = [r for r in ordered if _sort_key(sort_field)(r)[0] == 1]
        ordered = list(reversed(present)) + missing
    return ordered


# The portal's `strDealConfidence` filter accepts only the five colour values, so
# "which deals have no confidence set?" — 422 of 603 opportunities, the single
# largest group in the pipeline — was unanswerable. Asked for it, the model sent an
# empty string, got every opportunity back, and told the user to export the list and
# filter it by hand.
#
# The API cannot express it, so we do: sweep the unfiltered pages and keep the rows
# whose confidence is blank. Same technique `get_pipeline_analysis` already uses to
# compute totals the API won't.
_UNSET_FILTER_WORDS = frozenset({
  "(not set)", "not set", "notset", "none", "unset", "unassigned", "blank",
  "empty", "null", "no confidence", "not assigned",
})

# Bounds for the local sweep. 10 x 200 covers a pipeline several times the current
# 603 without letting a runaway filter walk the whole CRM. Shared with the analysis
# sweep via settings: they were identical values under different names, so raising
# one left the other behind and the two disagreed about how much they had seen.
_UNSET_SWEEP_PAGE_SIZE = settings.sweep_page_size
_UNSET_SWEEP_MAX_PAGES = settings.sweep_max_pages


def _is_unset_request(value: str) -> bool:
  return str(value or "").strip().lower() in _UNSET_FILTER_WORDS


def _has_no_confidence(row: Mapping[str, Any]) -> bool:
  confidence = str(row.get("dealConfidence") or "").strip().lower()
  return confidence in ("", "(not set)", "none", "null")


# ---------------------------------------------------------------------------
# Filter reconciliation
#
# The dashboard endpoint answers an unrecognised filter value with ZERO ROWS, not
# an error. Every filter that takes a vocabulary therefore has the same failure
# mode, and it is the worst kind: an empty result is indistinguishable from an
# empty pipeline, so the agent reports "there are none" with total confidence.
#
# Twice this reached users — "Rowan Adler has no opportunities" beside a
# portal listing five, and "there are no closed deals" for a value that was a
# STAGE filed under `status`. Both were fixed one at a time. This handles the
# class: on an empty result, every supplied value is checked against the
# vocabulary the CRM itself publishes for it, corrected where that is
# unambiguous, and reported with the valid values where it is not.
# ---------------------------------------------------------------------------

# Filter args naming a PERSON, and the key each is listed under by
# `loadOpportunityFilterOptions`. The row we emit carries strRsmNm while the
# filter takes strRsm; nothing guarantees the two share a spelling.
_PEOPLE_FILTERS = {
    "rsm": "rsms",
    "zone_director": "zones",
    "area_director": "areaDirectors",
}

# Filter args whose values come from a Globus One code group.
_CODE_GROUP_FILTERS = {
    "stage": "CRMSTG",
    "deal_confidence": "CRMDCF",
    "product_interests": "CRMPRD",
}

# Args the endpoint reads as MM/DD/YYYY. Any other shape matches nothing, again
# silently — an ISO date is what a model reaches for unprompted.
_DATE_FILTERS = (
    "expected_close_from", "expected_close_to", "created_from", "created_to",
)

# Args with no vocabulary of their own. They can never be reported as WRONG —
# nothing says what a valid status is — but a value sitting in one may still be
# provably a value of some other field, which is the "Closed" case: not a status
# at all, the last of the seven stages. So they take part in the misfiling check
# and never in the validity check. Collapsing the two removed the Closed fix.
_UNVALIDATED_FILTERS = ("status",)

# Free text and numbers. Nothing to compare against, nowhere for them to belong:
# left exactly as supplied.
_OPAQUE_FILTERS = ("account_name", "value_min", "value_max", "opp_name", "quarters")

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_US_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")

# A trailing "(Italy)" is territory shown beside the name, not part of it.
_NAME_SUFFIX_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _canonical_person(value: Any) -> str:
    return _NAME_SUFFIX_RE.sub("", str(value or "")).strip().casefold()


def _match_person(supplied: str, options: list[Any]) -> Optional[str]:
    """The advertised value a supplied name means, or None if it means none.

    Exact first, then ignoring case and any parenthesised territory, then a
    unique containment — "Adler" for "Rowan Adler (Italy)". Ambiguity
    resolves to None: guessing between two RSMs is worse than saying so.
    """
    for option in options:
        if str(option) == supplied:
            return str(option)
    target = _canonical_person(supplied)
    if not target:
        return None
    equal = [str(o) for o in options if _canonical_person(o) == target]
    if len(equal) == 1:
        return equal[0]
    partial = [
        str(o) for o in options
        if target in _canonical_person(o) or _canonical_person(o) in target
    ]
    return partial[0] if len(partial) == 1 else None


def _match_code(supplied: str, options: list[str]) -> Optional[str]:
    """The coded value a supplied string means, ignoring case and spacing."""
    target = " ".join(str(supplied or "").split()).casefold()
    if not target:
        return None
    for option in options:
        if " ".join(str(option).split()).casefold() == target:
            return str(option)
    return None


def _normalise_date(value: str) -> Optional[str]:
    """MM/DD/YYYY for a date the endpoint will match, or None if unrecognisable."""
    text = str(value or "").strip()
    iso = _ISO_DATE_RE.match(text)
    if iso:
        year, month, day = iso.groups()
        return f"{month}/{day}/{year}"
    us = _US_DATE_RE.match(text)
    if us:
        month, day, year = us.groups()
        return f"{int(month):02d}/{int(day):02d}/{year}"
    return None


async def _code_names(rt: Runtime, auth: Any, group: str) -> list[str]:
    """Labels in one code group, from the CRM rather than a list we maintain.

    `client.post` UNWRAPS the envelope — for this endpoint it returns the
    `lstCodeLookUp` list itself, not the dict around it (see GlobusClient.unwrap,
    and context.lookup_codes, which iterates the result directly). Treating the
    result as an envelope raised AttributeError the moment the list was
    non-empty, and passed only while it was empty. Both shapes are accepted here
    because unwrap has fallbacks that can still return a dict.
    """
    try:
        payload = await rt.client.post("loadCodeLookup", {"codeGrp": group}, auth=auth)
    except Exception:  # noqa: BLE001 — a diagnosis must not break the query
        return []
    if isinstance(payload, dict):
        payload = payload.get("lstCodeLookUp") or []
    if not isinstance(payload, list):
        return []
    return [
        str(r.get("codeNm")).strip()
        for r in payload
        if isinstance(r, dict) and r.get("codeNm")
    ]


async def _stage_names(rt: Runtime, auth: Any) -> list[str]:
    """Valid stage values. Kept as its own name — the stage list is the one other
    code reads directly."""
    return await _code_names(rt, auth, _CODE_GROUP_FILTERS["stage"])


# The values each filter can take, learned from the pipeline itself. The portal
# publishes only the people (loadOpportunityFilterOptions), and the code lookups
# for stages, products and deal confidence return nothing for this login. So a
# small model could not know that a word like "closed" is a stage value while
# "won" is a status value — and it spent eight calls guessing. Read from the rows
# the dashboard returns, per user (visibility differs), and kept for a while.
_LEARNED_FIELDS = {
    "stage": "strStage",
    "status": "strStatus",
    "deal_confidence": "strDealConfidence",
    "product_interests": "strProductInterests",
}
_LEARNED_CACHE: dict[str, tuple[float, dict[str, list[str]]]] = {}
_LEARNED_TTL_SECONDS = 1800
_SWEEP_PAGE_SIZE = 200
_SWEEP_MAX_PAGES = 10


async def _pipeline_values(rt: Runtime, auth: Any) -> dict[str, list[str]]:
    """Distinct stage / status / deal-confidence / product values in the pipeline."""
    user = str(getattr(getattr(auth, "identity", None), "user_id", "") or "")
    cached = _LEARNED_CACHE.get(user)
    if cached and time.monotonic() - cached[0] < _LEARNED_TTL_SECONDS:
        return cached[1]
    seen: dict[str, set[str]] = {arg: set() for arg in _LEARNED_FIELDS}
    try:
        scope = await company_scope(rt, auth)
        for page in range(1, _SWEEP_MAX_PAGES + 1):
            env = await rt.client.post_envelope(
                "crm/opp/loadOpportunityDashboard",
                {"strPage": str(page), "strPageSize": str(_SWEEP_PAGE_SIZE),
                 "strCompanyId": scope.ids}, auth=auth)
            rows = (env.get("lstDefault") or []) if isinstance(env, dict) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for arg, field in _LEARNED_FIELDS.items():
                    value = row.get(field)
                    if not value:
                        continue
                    parts = str(value).split(",") if arg == "product_interests" else [value]
                    seen[arg].update(str(p).strip() for p in parts if str(p).strip())
            try:
                total = int(env.get("strMessage"))
            except (AttributeError, TypeError, ValueError):
                total = 0
            if len(rows) < _SWEEP_PAGE_SIZE or page * _SWEEP_PAGE_SIZE >= total:
                break
    except Exception:  # noqa: BLE001 — learning values must never break a query
        pass
    values = {arg: sorted(found) for arg, found in seen.items()}
    if any(values.values()):
        _LEARNED_CACHE[user] = (time.monotonic(), values)
    return values


async def _vocabularies(rt: Runtime, auth: Any) -> dict[str, list[str]]:
    """Every filter arg the CRM publishes values for, mapped to those values.

    Fetched together rather than one at a time: this runs only on an empty
    result, and four sequential round trips would make a wrong answer slow as
    well as wrong.
    """
    async def options() -> dict[str, list[str]]:
        try:
            raw = await rt.client.post(
                "crm/opp/loadOpportunityFilterOptions", {}, auth=auth) or {}
        except Exception:  # noqa: BLE001
            return {}
        catalog = raw.get("objResult") or raw
        return {
            arg: [str(v) for v in (catalog.get(key) or [])]
            for arg, key in _PEOPLE_FILTERS.items()
        }

    groups = list(_CODE_GROUP_FILTERS.items())
    # Each fetch swallows its own failure and returns an empty vocabulary — this
    # path exists to IMPROVE an empty result and must never be able to replace it
    # with an error. Concurrent because it runs on a result that is already
    # empty: four sequential round trips would make a wrong answer slow too.
    results = await asyncio.gather(
        options(), *[_code_names(rt, auth, grp) for _arg, grp in groups]
    )
    vocab: dict[str, list[str]] = dict(results[0] or {})
    for (arg, _grp), names in zip(groups, results[1:]):
        vocab[arg] = names
    # Where the code lookups published nothing, the values the pipeline actually
    # carries — which is also the only source for status.
    learned = await _pipeline_values(rt, auth)
    for arg, values in learned.items():
        if values and not vocab.get(arg):
            vocab[arg] = values
    return vocab


def _match_for(arg: str, value: str, vocabulary: list[str]) -> Optional[str]:
    return (_match_person(value, vocabulary) if arg in _PEOPLE_FILTERS
            else _match_code(value, vocabulary))


async def _reconcile_filters(
    rt: Runtime, auth: Any, args: dict[str, str],
) -> tuple[dict[str, str], dict[str, Any], dict[str, str]]:
    """Check every supplied filter value against what the CRM will match.

    Three outcomes per filter, and the difference between them is the whole
    point:

      corrected   the value is right but spelled differently, or was filed under
                  the wrong arg — "Closed" sent as a status is a STAGE. Retried.
      unmatched   the value belongs to no vocabulary. Reported with the valid
                  ones, so the answer can say "not a value I can filter by".
      recognised  the value is valid and still matched nothing. Reported too: we
                  cannot tell that from an empty pipeline, so we must not claim
                  either.

    Anything in _UNCHECKABLE_FILTERS is left alone — a free-text account name or
    an unpublished status cannot be proven wrong, and guessing would be worse
    than saying nothing.
    """
    supplied = {
        k: v for k, v in args.items()
        if str(v or "").strip() and k not in _OPAQUE_FILTERS
    }
    if not supplied:
        return {}, {}, {}

    corrected: dict[str, str] = {}
    unmatched: dict[str, Any] = {}
    recognised: dict[str, str] = {}

    # Dates first: they need no round trip.
    for arg in _DATE_FILTERS:
        value = supplied.pop(arg, None)
        if value is None:
            continue
        normalised = _normalise_date(value)
        if normalised is None:
            unmatched[arg] = {"requested": value, "expectedFormat": "MM/DD/YYYY"}
        elif normalised != value:
            corrected[arg] = normalised

    if not supplied:
        return corrected, unmatched, recognised

    vocab = await _vocabularies(rt, auth)
    for arg, value in supplied.items():
        options = vocab.get(arg) or []
        resolved = _match_for(arg, value, options) if options else None
        if resolved is not None:
            recognised[arg] = resolved
            if resolved != value:
                corrected[arg] = resolved
            continue

        # Not a value for the arg it arrived on. It may still be one the CRM
        # knows under a different arg — "Closed" is a STAGE, whatever field it
        # was sent in. Moving it is what makes the question answerable, and it
        # only applies when the destination is free.
        moved_to = next(
            (other for other, names in vocab.items()
             if other != arg and names and _match_for(other, value, names)),
            None,
        )
        if moved_to and not str(args.get(moved_to) or "").strip():
            corrected[arg] = ""
            corrected[moved_to] = _match_for(moved_to, value, vocab[moved_to])
        elif options:
            # Only claimable when the CRM published a list to judge against. No
            # list means we do not know, and "I do not know" must not be
            # reported as "that value is wrong".
            unmatched[arg] = {"requested": value, "validValues": options}
    return corrected, unmatched, recognised



async def list_opportunities(
    rt: Runtime,
    deal_confidence: str = "",
    rsm: str = "",
    zone_director: str = "",
    area_director: str = "",
    stage: str = "",
    status: str = "",
    product_interests: str = "",
    account_name: str = "",
    expected_close_from: str = "",
    expected_close_to: str = "",
    created_from: str = "",
    created_to: str = "",
    value_min: str = "",
    value_max: str = "",
    opp_name: str = "",
    quarters: str = "",
    company: str = "",
    page: int = 1,
    page_size: int = 50,
    sort_field: str = "dealConfidence",
    sort_dir: str = "asc",
    call_ctx: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Search opportunities with CRM filters, returning rows already sorted.

    deal_confidence ∈ {Blue, Green, Orange, Yellow, Red}, or "(not set)" for the
    opportunities that have no confidence assigned — the largest group in the
    pipeline. rsm/zone_director/area_director are names (see get_filter_options);
    dates are MM/DD/YYYY. company narrows to one or more of your companies (name
    or id); empty covers all of them, as the Opportunities grid does.

    For "top N by value" pass sort_field="value", sort_dir="desc" — rows come back
    in exact descending order (numeric, blanks last), so take the first N as-is.
    Other sortable fields: name, account, stage, status, probability,
    dealConfidence, rsm, zoneDirector, areaDirector, quarter, expectedCloseDate.

    Returns the matching total, the sort applied, and the trimmed rows.
    """
    auth = await rt.context(call_ctx)
    scope = await company_scope(rt, auth, company)
    if scope.unmatched:
        return unmatched_company_result(
            scope, {"total": 0, "returned": 0, "page": page, "opportunities": []})
    unset_only = _is_unset_request(deal_confidence)
    args = dict(deal_confidence="" if unset_only else deal_confidence,
                rsm=rsm, zone_director=zone_director,
                area_director=area_director, stage=stage, status=status,
                product_interests=product_interests, account_name=account_name,
                expected_close_from=expected_close_from, expected_close_to=expected_close_to,
                created_from=created_from, created_to=created_to,
                value_min=value_min, value_max=value_max,
                opp_name=opp_name, quarters=quarters)
    body: dict[str, Any] = {_FILTER_MAP[k]: v for k, v in args.items()}
    body["strCompanyId"] = scope.ids
    if unset_only:
        return await _list_unset_confidence(
            rt, auth, body, page=page, page_size=page_size,
            sort_field=sort_field, sort_dir=sort_dir, companies=scope.names,
        )
    body.update(strPage=str(page), strPageSize=str(page_size),
                strSortField=sort_field, strSortDir=sort_dir)

    env = await rt.client.post_envelope("crm/opp/loadOpportunityDashboard", body, auth=auth)
    rows = env.get("lstDefault") or []

    # No rows AND a person named in the filter: the name may simply not be one
    # this endpoint matches. Checked only here, so a query that found something
    # pays nothing for it — and zero rows is precisely the case that needs
    # telling apart from "that person has no deals".
    # An empty result is the only case worth investigating, and the only one that
    # can be a wrong answer rather than a slow one — so nothing below costs a
    # successful query anything.
    unmatched: dict[str, Any] = {}
    recognised: dict[str, str] = {}
    if not rows:
        corrections, unmatched, recognised = await _reconcile_filters(rt, auth, args)
        if corrections:
            body.update({_FILTER_MAP[k]: v for k, v in corrections.items()})
            env = await rt.client.post_envelope(
                "crm/opp/loadOpportunityDashboard", body, auth=auth)
            rows = env.get("lstDefault") or []

    # Dashboard returns the *total* matching count in strMessage; rows are one page.
    try:
        total = int(env.get("strMessage"))
    except (TypeError, ValueError):
        total = len(rows)
    trimmed = _sorted_rows([_row(r, scope.names) for r in rows], sort_field, sort_dir)
    result = {"total": total, "returned": len(trimmed), "page": page,
              "sortedBy": f"{sort_field} {sort_dir}" if sort_field else None,
              "opportunities": trimmed}
    if recognised and not rows and not unmatched:
        # The name is one this CRM offers as a filter and it still matched
        # nothing. "No deals" is the wrong reading — the record may simply not be
        # in the set this endpoint returns for this user, which is how one RSM
        # read as having an empty pipeline beside a portal page listing five of
        # their deals.
        result["recognisedButEmpty"] = dict(recognised)
        result["caveat"] = (
            ", ".join(f"{k}={v!r}" for k, v in sorted(recognised.items()))
            + " is a value this CRM offers as a filter, and no matching "
            "opportunities came back. Say that no opportunities are VISIBLE for "
            "that selection and that the records may sit outside what this view "
            "returns — do NOT state that there are none, and do not speculate "
            "about why."
        )
    if unmatched and not rows:
        # Without this the caller cannot tell an unfilterable name from an empty
        # pipeline, and answers the second when it is the first.
        result["unmatchedFilters"] = unmatched
        result["caveat"] = (
            "No opportunities matched, and the "
            + ", ".join(sorted(unmatched))
            + " value(s) given are not among the ones this CRM filters by — see "
            "unmatchedFilters. Do NOT report this as there being no deals; say "
            "the value was not recognised and offer the valid ones."
        )
    return result


async def _list_unset_confidence(
    rt: Runtime,
    auth: Any,
    body: dict[str, Any],
    *,
    page: int,
    page_size: int,
    sort_field: str,
    sort_dir: str,
    companies: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Rows with no deal confidence, swept and filtered locally.

    `total` is the count of MATCHING rows — the unset ones — not the unfiltered
    sweep size. Reporting the sweep total would put "200 of 603" under an answer
    about 422 deals, which is how this looked before the filter existed.
    """
    body = dict(body)
    # Sweep in a stable order so paging cannot repeat or skip a row; the caller's
    # requested sort is applied to the filtered result afterwards.
    body.update(strSortField="name", strSortDir="asc",
                strPageSize=str(_UNSET_SWEEP_PAGE_SIZE))

    matched: list[dict[str, Any]] = []
    swept = 0
    total_unfiltered = 0
    truncated = False
    for sweep_page in range(1, _UNSET_SWEEP_MAX_PAGES + 1):
        body["strPage"] = str(sweep_page)
        env = await rt.client.post_envelope(
            "crm/opp/loadOpportunityDashboard", body, auth=auth
        )
        page_rows = env.get("lstDefault") or []
        if sweep_page == 1:
            try:
                total_unfiltered = int(env.get("strMessage"))
            except (TypeError, ValueError):
                total_unfiltered = len(page_rows)
        swept += len(page_rows)
        matched.extend(r for r in (_row(x, companies) for x in page_rows)
                       if _has_no_confidence(r))
        if len(page_rows) < _UNSET_SWEEP_PAGE_SIZE or swept >= total_unfiltered:
            break
        if sweep_page == _UNSET_SWEEP_MAX_PAGES:
            truncated = True

    ordered = _sorted_rows(matched, sort_field, sort_dir)
    start = max(page - 1, 0) * page_size
    return {
        "total": len(ordered),
        "returned": len(ordered[start:start + page_size]),
        "page": page,
        "sortedBy": f"{sort_field} {sort_dir}" if sort_field else None,
        "filter": "deal confidence not set",
        "sweptRows": swept,
        "truncated": truncated,
        "opportunities": ordered[start:start + page_size],
    }


# ---------------------------------------------------------------------------
# Per-opportunity lookups
#
# The API answers an unknown or invisible opportunity id with an error status,
# which GlobusClient.unwrap turns into a bare `[]` — the same shape as "this deal
# has no quotes". Live, `get_opportunity` returned `[]`; the model could not tell
# "does not exist" from "not visible to you" from "failed", went hunting through
# 200 rows of `list_opportunities` for the id (which that tool cannot filter by),
# circled, and the turn ended in an error. The same session answered "has no
# quotes linked to it yet" — a confident claim built on an empty result that
# meant only that nothing came back.
#
# So an empty result here always says what it means.
# ---------------------------------------------------------------------------

def _opportunity_not_found(opp_id: int) -> dict[str, Any]:
    return {
        "found": False,
        "oppId": opp_id,
        "caveat": (
            f"No opportunity with id {opp_id} is visible to you — it may not "
            "exist, or it may sit outside what your account can see. Tell the "
            "user exactly that. Do NOT search list_opportunities for it: that "
            "tool cannot look records up by id, and scanning its pages will not "
            "find one this lookup could not."
        ),
    }


async def _opportunity_exists(rt: Runtime, auth: Any, opp_id: int) -> bool:
    try:
        detail = await rt.client.get(f"crm/opp/getOpportunityDetail/{opp_id}", auth=auth)
    except Exception:  # noqa: BLE001 — the check must not replace the answer
        return False
    return isinstance(detail, dict) and bool(detail)


async def get_opportunity(rt: Runtime, opp_id: int,
                          call_ctx: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Full detail for one opportunity by id — its stage, status, value, owner.

    Returns `found: false` with a caveat when no such opportunity is visible,
    rather than an empty result the caller would have to interpret.
    """
    auth = await rt.context(call_ctx)
    await rt.codes.ensure_defaults(auth=auth)
    detail = await rt.client.get(f"crm/opp/getOpportunityDetail/{opp_id}", auth=auth)
    if isinstance(detail, dict) and detail:
        return rt.codes.relabel(detail, ("STAGEID",))
    return _opportunity_not_found(opp_id)


async def get_opportunity_stats(rt: Runtime, expected_close_from: str = "",
                                expected_close_to: str = "",
                                call_ctx: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Pipeline figures: called deals, called value, chasing-for-quarter, closed."""
    auth = await rt.context(call_ctx)
    # The DAO scopes these figures to this close-date range (plus the caller's
    # territory). ISO dates were being sent and matched nothing.
    body = {"strExpectedCloseFrom": _normalise_date(expected_close_from) or expected_close_from,
            "strExpectedCloseTo": _normalise_date(expected_close_to) or expected_close_to}
    return await rt.client.post("crm/opp/loadOpportunityStats", body, auth=auth) or {}


async def get_filter_options(rt: Runtime,
                             call_ctx: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """The valid value for every opportunity filter: RSMs, area directors and zones
    (from the portal), and the stages, statuses, deal confidences and product
    interests that exist in your pipeline."""
    auth = await rt.context(call_ctx)
    people = await rt.client.post("crm/opp/loadOpportunityFilterOptions", {}, auth=auth) or {}
    learned = await _pipeline_values(rt, auth)
    return {
        **(people if isinstance(people, dict) else {}),
        "stages": learned.get("stage", []),
        "statuses": learned.get("status", []),
        "dealConfidences": learned.get("deal_confidence", []),
        "productInterests": learned.get("product_interests", []),
    }


async def _sub(rt: Runtime, path: str, opp_id: int,
               call_ctx: Optional[Mapping[str, Any]], what: str = "records",
               query: str = "") -> Any:
    """A list hanging off one opportunity — quotes, products, notes, and so on.

    A non-empty result passes through untouched. An empty one is checked against
    the opportunity itself, because "has no quotes" and "is not visible to you"
    arrive as the same `[]` and mean opposite things. Costs one extra call, and
    only when there was nothing to show anyway.
    """
    auth = await rt.context(call_ctx)
    result = await rt.client.get(f"{path}/{opp_id}{query}", auth=auth)
    if result:
        return result
    if not await _opportunity_exists(rt, auth, opp_id):
        return _opportunity_not_found(opp_id)
    return {
        "found": True,
        "oppId": opp_id,
        what: [],
        "caveat": (
            f"Opportunity {opp_id} exists and has no {what} recorded. It is "
            f"accurate to say it has no {what}."
        ),
    }


async def get_opportunity_tasks(rt: Runtime, opp_id: int, call_ctx=None) -> Any:
    """Checklist tasks on an opportunity — each task, its owner, due date and status.

    `crm/task/loadChecklist`, which the Opportunity Detail and Tasks screens call.
    Taken from the codebase extract; the Tasks page offered no tool at all before.
    """
    return await _sub(rt, "crm/task/loadChecklist", opp_id, call_ctx, what="tasks")


async def get_task_notes(rt: Runtime, task_id: int, call_ctx=None) -> Any:
    """Notes on one checklist task, by task id (from get_opportunity_tasks)."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"crm/note/loadTaskNotes/{task_id}", auth=auth)


async def get_opportunity_products(rt: Runtime, opp_id: int, call_ctx=None) -> Any:
    """Product-interest lines on an opportunity."""
    return await _sub(rt, "crm/opp/support/loadProducts", opp_id, call_ctx,
                      what="products")


async def get_opportunity_quotes(rt: Runtime, opp_id: int, call_ctx=None) -> Any:
    """Quotes linked to an opportunity."""
    return await _sub(rt, "crm/opp/support/loadOppQuotes", opp_id, call_ctx,
                      what="quotes")


async def get_opportunity_activities(rt: Runtime, opp_id: int, call_ctx=None) -> Any:
    """Activity timeline for an opportunity."""
    return await _sub(rt, "crm/activity/loadActivities", opp_id, call_ctx,
                      what="activities")


async def get_opportunity_contacts(rt: Runtime, opp_id: int, call_ctx=None) -> Any:
    """Contacts / champions on an opportunity."""
    return await _sub(rt, "crm/contact/loadContacts", opp_id, call_ctx,
                      what="contacts")


async def get_opportunity_notes(rt: Runtime, opp_id: int, call_ctx=None) -> Any:
    """Free-text notes on an opportunity."""
    return await _sub(rt, "crm/note/loadNotes", opp_id, call_ctx,
                      what="notes")


async def get_opportunity_history(rt: Runtime, opp_id: int, page: int = 1,
                                  page_size: int = 10, call_ctx=None) -> Any:
    """Field-level change history for an opportunity."""
    return await _sub(
        rt, "crm/opp/support/loadChangeHistory", opp_id, call_ctx, what="history",
        query=f"?page={page}&pageSize={page_size}",
    )
