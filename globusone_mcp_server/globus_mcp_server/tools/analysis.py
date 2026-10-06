"""Pipeline analysis — computed fact sheets for report generation (Phase 1).

Why this module exists rather than letting the agent do the maths:

``list_opportunities`` returns ONE PAGE (50 rows by default) with the true match
count in a separate field, so any figure the model derives from a tool result is
computed over a page, not the pipeline. A report that says "total pipeline is
$X" has to see every row.

So this module pages through the dashboard itself, computes every number in
Python, and returns a compact **fact sheet**. The model never does arithmetic on
CRM data and never sees the raw rows — it reads the fact sheet and writes prose
around figures it cannot alter. That is the whole anti-fabrication design: a
wrong number in a polished report carries far more authority than a wrong number
in a chat reply.
"""

from __future__ import annotations

import os

from datetime import date, datetime
from typing import Any, Mapping, Optional

from . import Runtime
from ..config import settings
from .opportunities import _FILTER_MAP, _row

# Sweep limits come from settings so this module and `opportunities` cannot drift
# apart — they used to declare the same values under different names.
_SWEEP_PAGE_SIZE = settings.sweep_page_size
_MAX_SWEEP_PAGES = settings.sweep_max_pages
_MAX_SWEEP_ROWS = settings.sweep_max_rows

# Days without activity before an opportunity is called out as stale. A judgement
# call about this pipeline, not a fact — configurable so it can be tuned without a
# code change.
_STALE_DAYS = int(os.environ.get("GLOBUS_STALE_DAYS", "60"))

# Late stages — a deal here is close to a commitment, so low confidence at this
# point is the signal a forecast is quietly resting on.
_LATE_STAGES = ("drive alignment", "execute and deliver")

_WEAK_CONFIDENCE = ("red", "yellow")

# The five product interests the portal tracks.
_PRODUCTS = ("Robot", "E3D", "Cranial", "HUB", "EXR")


def _num(raw: Any) -> float:
    """Parse a CRM money/percent string to a float; unparseable → 0.0.

    Values arrive as strings and sometimes carry separators or a currency mark
    ("2,485,000", "$900000"). A blank or junk value must not abort a whole report,
    so it contributes zero and is counted separately as missing.
    """
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = "".join(c for c in str(raw) if c.isdigit() or c in ".-")
    try:
        return float(cleaned) if cleaned not in ("", "-", ".", "-.") else 0.0
    except ValueError:
        return 0.0


def _parse_date(raw: Any) -> Optional[date]:
    """Parse the portal's date strings; None if absent or unrecognised."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%b-%Y", "%m/%d/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _bucket(rows: list[dict], field: str) -> list[dict[str, Any]]:
    """Group rows by a field into count + value totals, largest value first.

    Blank values are grouped under "(not set)" rather than dropped — a report
    that silently omits 20 unassigned deals misstates the total.
    """
    acc: dict[str, dict[str, float]] = {}
    for r in rows:
        key = str(r.get(field) or "").strip() or "(not set)"
        slot = acc.setdefault(key, {"count": 0.0, "value": 0.0})
        slot["count"] += 1
        slot["value"] += _num(r.get("value"))
    out = [
        {"label": k, "count": int(v["count"]), "value": round(v["value"], 2)}
        for k, v in acc.items()
    ]
    out.sort(key=lambda d: d["value"], reverse=True)
    return out


def _product_mix(rows: list[dict]) -> list[dict[str, Any]]:
    """Attach rate per product.

    ``productInterests`` is a comma-joined string ("Robot,E3D,Cranial"), so a deal
    counts towards every product it names — these buckets intentionally sum to
    more than the deal count.
    """
    out = []
    for product in _PRODUCTS:
        matched = [
            r for r in rows
            if product.lower() in str(r.get("productInterests") or "").lower()
        ]
        if matched:
            out.append({
                "label": product,
                "count": len(matched),
                "value": round(sum(_num(r.get("value")) for r in matched), 2),
                "attachRate": round(100.0 * len(matched) / len(rows), 1) if rows else 0.0,
            })
    out.sort(key=lambda d: d["count"], reverse=True)
    return out


def _concentration(rows: list[dict], field: str, top_n: int) -> dict[str, Any]:
    """Share of total value held by the biggest N *named* groups of a field.

    The "(not set)" bucket is excluded deliberately. 323 of 441 opportunities
    carry no account name, so including it made "the top 5 accounts" a single
    heap of unnamed records and reported 60% concentration that means nothing.
    Unnamed records are counted separately so the answer can say so honestly
    instead of hiding them inside a metric.
    """
    buckets = [b for b in _bucket(rows, field) if b["label"] != "(not set)"]
    unset = [b for b in _bucket(rows, field) if b["label"] == "(not set)"]
    # Share is measured against the value that CAN be attributed — dividing by a
    # total that includes unattributed value would understate concentration.
    total = sum(b["value"] for b in buckets)
    top = buckets[:top_n]
    top_value = sum(b["value"] for b in top)
    return {
        "groups": top,
        "topValue": round(top_value, 2),
        "sharePct": round(100.0 * top_value / total, 1) if total else 0.0,
        "namedGroups": len(buckets),
        "attributedValue": round(total, 2),
        "unattributedRecords": unset[0]["count"] if unset else 0,
        "unattributedValue": unset[0]["value"] if unset else 0.0,
    }


def _is_open(row: dict) -> bool:
    """True if the deal is still live.

    A won/lost deal is finished, and every attention signal below would misfire on
    one: nobody logs activity on a closed deal (so it looks stale), and its close
    date is in the past by definition (so it looks overdue). Checks both status and
    stage, since either can carry the closed state.
    """
    status = str(row.get("status") or "").lower()
    stage = str(row.get("stage") or "").lower()
    return "clos" not in status and "won" not in status and "lost" not in status \
        and "clos" not in stage


def _attention(rows: list[dict], today: date) -> dict[str, Any]:
    """Deals that need a look: stale, overdue, or late-stage with weak confidence.

    Open deals only — see _is_open. Cheap to compute from the same rows and the
    first thing anyone asks after seeing pipeline totals. The full risk report
    (with weighting and its own register) is a later phase; this is the headline
    version.
    """
    stale, overdue, shaky = [], [], []
    for r in rows:
        if not _is_open(r):
            continue
        value = _num(r.get("value"))
        last = _parse_date(r.get("lastActivity"))
        if last is not None and (today - last).days >= _STALE_DAYS:
            stale.append({"name": r.get("name"), "value": value,
                          "daysSinceActivity": (today - last).days,
                          "rsm": r.get("rsm")})
        close = _parse_date(r.get("expectedCloseDate"))
        if close is not None and close < today:
            overdue.append({"name": r.get("name"), "value": value,
                            "expectedCloseDate": r.get("expectedCloseDate"),
                            "rsm": r.get("rsm")})
        stage = str(r.get("stage") or "").lower()
        conf = str(r.get("dealConfidence") or "").lower()
        if stage in _LATE_STAGES and conf in _WEAK_CONFIDENCE:
            shaky.append({"name": r.get("name"), "value": value,
                          "stage": r.get("stage"), "dealConfidence": r.get("dealConfidence"),
                          "rsm": r.get("rsm")})

    def pack(items: list[dict], label: str) -> dict[str, Any]:
        items.sort(key=lambda d: d["value"], reverse=True)
        return {
            "label": label,
            "count": len(items),
            "value": round(sum(i["value"] for i in items), 2),
            "deals": items[:10],
        }

    return {
        "stale": pack(stale, f"No activity in {_STALE_DAYS}+ days"),
        "overdue": pack(overdue, "Past expected close date, still open"),
        "lateStageWeakConfidence": pack(shaky, "Late stage, Red/Yellow confidence"),
    }


async def get_pipeline_analysis(
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
    value_min: str = "",
    value_max: str = "",
    call_ctx: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Analyse the WHOLE opportunity pipeline and return a computed fact sheet.

    Use this — not `list_opportunities` — whenever the user wants analysis, a
    report, a breakdown of the whole pipeline, or "how are we doing". It sweeps
    every matching page (`list_opportunities` only returns one) and computes the
    totals, the stage/confidence/quarter/product breakdowns, the biggest deals,
    concentration, and the deals needing attention.

    Pass the same filters as `list_opportunities` to analyse a slice (one RSM,
    one confidence colour); pass none to analyse everything.

    Every figure is computed here. Report the numbers as given and do NOT
    recompute, re-total or estimate any of them. Follow with
    `create_pipeline_report` if the user wants it as a document.
    """
    auth = await rt.context(call_ctx)
    args = dict(deal_confidence=deal_confidence, rsm=rsm, zone_director=zone_director,
                area_director=area_director, stage=stage, status=status,
                product_interests=product_interests, account_name=account_name,
                expected_close_from=expected_close_from,
                expected_close_to=expected_close_to,
                value_min=value_min, value_max=value_max)
    body: dict[str, Any] = {_FILTER_MAP[k]: v for k, v in args.items() if k in _FILTER_MAP}
    # Sweep in a stable order so paging can't return the same row twice or skip one.
    body.update(strSortField="name", strSortDir="asc",
                strPageSize=str(_SWEEP_PAGE_SIZE))

    rows: list[dict] = []
    total = 0
    truncated = False
    for page in range(1, _MAX_SWEEP_PAGES + 1):
        body["strPage"] = str(page)
        env = await rt.client.post_envelope("crm/opp/loadOpportunityDashboard", body, auth=auth)
        page_rows = env.get("lstDefault") or []
        if page == 1:
            try:
                total = int(env.get("strMessage"))
            except (TypeError, ValueError):
                total = len(page_rows)
        rows.extend(_row(r) for r in page_rows)
        if len(page_rows) < _SWEEP_PAGE_SIZE or len(rows) >= total:
            break
        if len(rows) >= _MAX_SWEEP_ROWS:
            truncated = True
            break
        # Running out of pages is a cap too. Without this the flag was correct
        # only because _MAX_SWEEP_ROWS happens to equal pages x page size —
        # raising GLOBUS_SWEEP_MAX_ROWS alone would truncate silently.
        if page == _MAX_SWEEP_PAGES:
            truncated = True

    if not rows:
        return {
            "reportKind": "pipeline_health",
            "scope": {"filters": {k: v for k, v in args.items() if v}, "total": 0,
                      "rowsAnalysed": 0, "truncated": False},
            "headline": {}, "note": "No opportunities matched these filters.",
        }

    today = date.today()
    values = [_num(r.get("value")) for r in rows]
    total_value = sum(values)
    missing_value = sum(1 for r in rows if _num(r.get("value")) <= 0)

    # Split open from closed. A result set can hold both (the Closed stage shows up
    # in the dashboard), and conflating them overstates what is still winnable.
    open_rows = [r for r in rows if _is_open(r)]
    closed_rows = [r for r in rows if not _is_open(r)]
    open_value = sum(_num(r.get("value")) for r in open_rows)
    # Weighted value: probability is a percentage on each row. Open deals only —
    # a closed-won deal at 100% is revenue, not forecast — so this is the number a
    # forecast should quote, and it is always lower than the open total.
    weighted = sum(
        _num(r.get("value")) * _num(r.get("probability")) / 100.0 for r in open_rows
    )

    top_deals = sorted(rows, key=lambda r: _num(r.get("value")), reverse=True)[:10]

    # Every figure below is computed over `rows`, which is the analysed set and
    # not necessarily the pipeline. When they differ, say so in the payload
    # itself: the document builder already prints a caveat, but the chat answer
    # reads these numbers straight off the sheet and had nothing to warn it.
    caveat = (
        f"These figures describe the {len(rows):,} records analysed, not the "
        f"{total:,} that matched — the sweep was capped. Say so when quoting them."
    ) if truncated else ""

    return {
        "reportKind": "pipeline_health",
        **({"caveat": caveat} if caveat else {}),
        # The records this analysis was computed from. NOT for the model to read —
        # the agent's after_tool_callback lifts them out for the CSV download and
        # strips them from the payload before the model sees it, so a breakdown
        # answer still offers every matching record without spending context on
        # hundreds of rows. (Before this, an aggregate answer had no records to
        # download at all, and the button fell back to the summary on screen.)
        "rows": rows,
        "scope": {
            "filters": {k: v for k, v in args.items() if v},
            "total": total,
            "rowsAnalysed": len(rows),
            "truncated": truncated,
            "generatedOn": today.isoformat(),
        },
        "headline": {
            "opportunities": len(rows),
            "totalValue": round(total_value, 2),
            "openOpportunities": len(open_rows),
            "openValue": round(open_value, 2),
            "closedOpportunities": len(closed_rows),
            "closedValue": round(total_value - open_value, 2),
            "weightedOpenValue": round(weighted, 2),
            "averageDealSize": round(total_value / len(rows), 2),
            "largestDeal": round(max(values), 2) if values else 0.0,
            "dealsWithoutValue": missing_value,
        },
        "byStage": _bucket(rows, "stage"),
        "byConfidence": _bucket(rows, "dealConfidence"),
        "byQuarter": _bucket(rows, "quarter"),
        "byRsm": _bucket(rows, "rsm"),
        "productMix": _product_mix(rows),
        "topDeals": [
            {"name": r.get("name"), "account": r.get("account"),
             "value": _num(r.get("value")), "stage": r.get("stage"),
             "dealConfidence": r.get("dealConfidence"), "rsm": r.get("rsm")}
            for r in top_deals
        ],
        "concentration": {
            "topAccounts": _concentration(rows, "account", 5),
            "topRsms": _concentration(rows, "rsm", 3),
        },
        "attention": _attention(rows, today),
    }
