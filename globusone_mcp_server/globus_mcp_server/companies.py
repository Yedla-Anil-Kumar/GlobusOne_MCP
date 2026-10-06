"""Which companies a request covers.

Every POST carries the identity's `strCompanyId` — the user's ONE home company
(1000, Globus Medical North America). loadTasks and loadOpportunityDashboard
read that field as a company filter (CSV), so every task and opportunity answer
was limited to that one company while the portal's grids list all of them: 1
task "In Progress" against the grid's 11, 0 for an Italian owner with 12.

Left empty, strCompanyId is sent EMPTY, which those DAOs read as "no company
filter" — the grids' default. Sending the companies from `loadCompany` instead
was not the same: for a live login it returned only 1000 while the Tasks grid
listed other companies' tasks too. What a user may see is still decided by the
DAO's owner/territory hierarchy, not by this field.

A `company` argument (name or id, several comma-separated) narrows to those
companies; names are resolved against `loadCompany/{partyId}/105401,105400`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

# The party types the portal's own loadCompany call sends.
_PARTY_TYPES = "105401,105400"
_TTL_SECONDS = 1800
_cache: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}


@dataclass(frozen=True)
class CompanyScope:
    """`ids` goes into strCompanyId; `names` labels rows; `unmatched` is set when
    a requested company is not one the user has."""

    ids: str
    names: dict[str, str] = field(default_factory=dict)
    unmatched: Optional[dict[str, Any]] = None

    @property
    def id_list(self) -> list[str]:
        return [i for i in self.ids.split(",") if i]


def _identity(auth: Any) -> Any:
    return getattr(auth, "identity", None)


async def user_companies(rt: Any, auth: Any) -> dict[str, str]:
    """Company id → name for every company the signed-in user can see. Never raises."""
    identity = _identity(auth)
    party = str(getattr(identity, "party_id", "") or "")
    if not party:
        return {}
    key = (str(getattr(identity, "user_id", "") or ""), party)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < _TTL_SECONDS:
        return cached[1]
    try:
        rows = await rt.client.get(f"loadCompany/{party}/{_PARTY_TYPES}", auth=auth)
    except Exception as exc:  # noqa: BLE001 — scoping must not break the query
        logger.warning(f"[companies] loadCompany failed for party {party}: {exc}")
        return {}
    companies = {
        str(r["companyId"]): str(r.get("companyName") or r["companyId"])
        for r in (rows if isinstance(rows, list) else [])
        if isinstance(r, dict) and r.get("companyId") not in (None, "")
    }
    if companies:
        _cache[key] = (time.monotonic(), companies)
        logger.info(f"[companies] party {party}: {','.join(companies)}")
    else:
        logger.warning(f"[companies] loadCompany returned no companies for party {party}")
    return companies


def _match(requested: str, companies: dict[str, str]) -> Optional[str]:
    """The company id a name or id means: exact, then ignoring case, then a unique
    part of a name ("North America")."""
    text = " ".join(requested.split())
    if text in companies:
        return text
    target = text.casefold()
    for cid, name in companies.items():
        if " ".join(name.split()).casefold() == target:
            return cid
    partial = [cid for cid, name in companies.items() if target in name.casefold()]
    return partial[0] if len(partial) == 1 else None


async def company_scope(rt: Any, auth: Any, company: str = "", *,
                        one_by_default: bool = False) -> CompanyScope:
    """The strCompanyId to send.

    Empty `company`: "" (no company filter) — or, with `one_by_default`, the home
    company, which a single-company screen (the quote dashboard) opens on and
    requires.
    """
    home = str(getattr(_identity(auth), "company_id", "") or "")
    companies = await user_companies(rt, auth)
    wanted = [p.strip() for p in str(company or "").split(",") if p.strip()]
    if not wanted:
        return CompanyScope(ids=home if one_by_default else "", names=companies)

    ids: list[str] = []
    unknown: list[str] = []
    for part in wanted:
        cid = _match(part, companies)
        if cid is None and part.isdigit() and not companies:
            cid = part                     # nothing to check it against
        if cid is None:
            unknown.append(part)
        elif cid not in ids:
            ids.append(cid)
    unmatched = None
    if unknown:
        unmatched = {"requested": ", ".join(unknown),
                     "validValues": sorted(companies.values())}
    return CompanyScope(ids=",".join(ids), names=companies, unmatched=unmatched)


def unmatched_company_result(scope: CompanyScope, empty: dict[str, Any]) -> dict[str, Any]:
    """The reply when a requested company is not one the user has. Nothing queried."""
    valid = ", ".join(scope.unmatched["validValues"]) or "none could be read"
    return {
        **empty,
        "unmatchedFilters": {"company": scope.unmatched},
        "caveat": (f"{scope.unmatched['requested']!r} is not one of your companies "
                   f"({valid}). Nothing was queried — say so and offer those; do not "
                   "report this as there being no records."),
    }


def clear_cache() -> None:
    _cache.clear()
