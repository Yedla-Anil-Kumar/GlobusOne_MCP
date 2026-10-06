"""CRM task, search and notification read tools (the Tasks screen and global search).

Covers GmCrmTaskController (crm/task/*), the task activity timeline
(crm/activity/loadTaskActivities), GmCrmSearchController (crm/search/*) and the
notification preferences (crm/notifPref). Taken from the portal's codebase
extract: endpoint, method, path/query inputs, the GmTaskDashboardModel field
names, and where each response keeps its data.

Filter values, from GmCrmTaskSqlBuilder: every multi-value filter is CSV and
matched exactly — statuses by NAME (code group CRMTSK, e.g. "Completed"),
priorities and owners by id or full name, related types by id or name
(Opportunity, Account, Lead, Contact). get_task_filter_options gives the valid
values. Only loadTasks applies them; stats and filter options apply the owner
hierarchy alone.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional
from urllib.parse import quote, urlencode

from . import Runtime
from ..companies import company_scope, unmatched_company_result
from .opportunities import _normalise_date

# GmTaskDashboardModel.strQuickFilter — the Tasks screen's quick-filter chips.
QUICK_FILTERS = ("open", "my-open", "due-today", "overdue", "completed-week")

# One record's tasks: strEntityId + strRelatedTypes by NAME. strEntityType is
# compared to a numeric column, so a name there would match nothing — it is left
# out, as the codebase notes direct.
OPPORTUNITY_RELATED_TYPE = "Opportunity"


def _date(value: str) -> str:
    return (_normalise_date(value) or value) if str(value or "").strip() else ""


def _task_filters(statuses: str, priorities: str, owners: str, related_types: str,
                  due_from: str, due_to: str, created_from: str, created_to: str,
                  search: str) -> dict[str, str]:
    return {
        "strStatuses": statuses, "strPriorities": priorities, "strOwners": owners,
        "strRelatedTypes": related_types,
        "strDueFrom": _date(due_from), "strDueTo": _date(due_to),
        "strCreatedDateFrom": _date(created_from), "strCreatedDateTo": _date(created_to),
        "strSearch": search,
    }


def _identity_query(auth: Any) -> str:
    """`?userId=…&accessLevel=…` — the search and task-detail GETs take these as
    query params, not in a body."""
    identity = getattr(auth, "identity", None)
    return "?" + urlencode({"userId": getattr(identity, "user_id", "") or "",
                            "accessLevel": getattr(identity, "access_level", "") or ""})


def _rows_and_total(env: Any) -> tuple[list, int]:
    rows = (env.get("lstDefault") or []) if isinstance(env, dict) else []
    try:
        total = int(env.get("strMessage"))
    except (AttributeError, TypeError, ValueError):
        total = len(rows)
    return rows, total


# -- the Tasks screen -----------------------------------------------------
async def list_tasks(rt: Runtime, quick_filter: str = "", statuses: str = "",
                     priorities: str = "", owners: str = "", related_types: str = "",
                     due_from: str = "", due_to: str = "",
                     created_from: str = "", created_to: str = "", search: str = "",
                     company: str = "", sort_field: str = "", sort_dir: str = "",
                     page: int = 1, page_size: int = 50,
                     call_ctx: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """The Tasks list — each task with its owner (strOwnerName), status, priority
    and due date. quick_filter ∈ {open, my-open, due-today, overdue,
    completed-week}; statuses are names ("Completed"); priorities, owners and
    related_types (Opportunity, Account, Lead, Contact) are ids or names; several
    values comma-separated; search matches title or owner name; dates MM/DD/YYYY.
    company narrows to one or more of your companies (name or id); leave it
    empty for all of them, as the Tasks grid shows.
    `total` is the full filtered count — use it for "how many". Checklist items
    are not tasks: see get_opportunity_tasks."""
    quick = str(quick_filter or "").strip().lower()
    if quick and quick not in QUICK_FILTERS:
        # Checked here rather than sent: an unknown value would come back as an
        # empty list, which reads exactly like "no tasks".
        return {"total": 0, "tasks": [],
                "unmatchedFilters": {"quick_filter": {"requested": quick_filter,
                                                      "validValues": list(QUICK_FILTERS)}},
                "caveat": (f"{quick_filter!r} is not a Tasks quick filter. The valid ones "
                           f"are: {', '.join(QUICK_FILTERS)}. Nothing was queried — do "
                           "not report this as there being no tasks.")}
    auth = await rt.context(call_ctx)
    scope = await company_scope(rt, auth, company)
    if scope.unmatched:
        return unmatched_company_result(scope, {"total": 0, "tasks": []})
    body = {
        **_task_filters(statuses, priorities, owners, related_types,
                        due_from, due_to, created_from, created_to, search),
        "strCompanyId": scope.ids,
        "strQuickFilter": quick,
        # "my-open" means the signed-in user's; the screen sends them as this.
        "strCurrentUserId": auth.identity.user_id,
        "strSortField": sort_field, "strSortDir": sort_dir,
        "strPage": str(page), "strPageSize": str(page_size),
    }
    rows, total = _rows_and_total(
        await rt.client.post_envelope("crm/task/loadTasks", body, auth=auth))
    return {"total": total, "returned": len(rows), "page": page, "tasks": rows}


async def list_opportunity_task_items(rt: Runtime, opp_id: int,
                                      call_ctx: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """Tasks linked to one opportunity — owner, status, due date. Different from
    get_opportunity_tasks, which is the opportunity's checklist."""
    auth = await rt.context(call_ctx)
    scope = await company_scope(rt, auth)
    body = {"strEntityId": str(opp_id), "strRelatedTypes": OPPORTUNITY_RELATED_TYPE,
            "strCompanyId": scope.ids, "strCurrentUserId": auth.identity.user_id}
    rows, total = _rows_and_total(
        await rt.client.post_envelope("crm/task/loadTasksByEntity", body, auth=auth))
    out: dict[str, Any] = {"oppId": opp_id, "total": total, "tasks": rows}
    if not rows:
        out["caveat"] = (
            f"Opportunity {opp_id} has no tasks visible to you. Checklist items are "
            "kept separately — get_opportunity_tasks lists those.")
    return out


async def get_task_stats(rt: Runtime,
                         call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """The Tasks stat cards — dueToday, overdue, completedWeek — over every task you
    can see. They take no filters; for a filtered count use list_tasks' total."""
    auth = await rt.context(call_ctx)
    # The DAO applies only the owner hierarchy here. Filters offered on this tool
    # would be silently ignored and the unfiltered counts reported as filtered.
    return await rt.client.post("crm/task/loadTaskStats",
                                {"strCurrentUserId": auth.identity.user_id}, auth=auth)


async def get_task_filter_options(rt: Runtime,
                                  call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """The values the Tasks filters offer — statuses, priorities, owners, related types."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("crm/task/loadTaskFilterOptions",
                                {"strCurrentUserId": auth.identity.user_id}, auth=auth)


async def get_task(rt: Runtime, task_id: int,
                   call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Full detail for one task by id."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"crm/task/getTaskDetail/{task_id}{_identity_query(auth)}",
                               auth=auth)


async def get_task_history(rt: Runtime, task_id: int, page: int = 1,
                           call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Field-level change history for one task."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"crm/task/loadChangeHistory/{task_id}?page={page}", auth=auth)


async def get_task_activities(rt: Runtime, task_id: int,
                              call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Activity timeline for one task."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"crm/activity/loadTaskActivities/{task_id}", auth=auth)


async def get_notification_preferences(rt: Runtime,
                                       call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """The signed-in user's CRM notification preferences."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"crm/notifPref/{auth.identity.user_id}", auth=auth)


# -- global search --------------------------------------------------------
async def search_opportunities(rt: Runtime, search: str,
                               call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Opportunities whose name matches `search`, within what you can see."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(
        f"crm/search/opportunities/{quote(str(search), safe='')}{_identity_query(auth)}",
        auth=auth)


async def search_contacts(rt: Runtime, search: str,
                          call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Contacts whose name matches `search`, within what you can see."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(
        f"crm/search/contacts/{quote(str(search), safe='')}{_identity_query(auth)}",
        auth=auth)
