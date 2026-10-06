"""Quote-metrics dashboard read tools (GmQuoteMetricsDashboardController).

All GET endpoints under ``quotes/*`` that take query params. Empty params are
dropped from the query string.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional
from urllib.parse import urlencode

from . import Runtime


def _qs(**params: str) -> str:
    clean = {k: v for k, v in params.items() if v not in ("", None)}
    return ("?" + urlencode(clean)) if clean else ""


async def get_quotes_by_period(rt: Runtime, start_date: str = "", end_date: str = "",
                               call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Quote counts/totals grouped by time period."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"quotes/by-period{_qs(startDate=start_date, endDate=end_date)}", auth=auth)


async def get_quotes_by_rep(rt: Runtime, start_date: str = "", end_date: str = "",
                            call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Quotes aggregated by sales rep."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"quotes/by-rep{_qs(startDate=start_date, endDate=end_date)}", auth=auth)


async def get_quotes_by_status(rt: Runtime, start_date: str = "", end_date: str = "",
                               call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Quotes aggregated by status."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"quotes/by-status{_qs(startDate=start_date, endDate=end_date)}", auth=auth)


async def get_quotes_by_revenue(rt: Runtime, start_date: str = "", end_date: str = "",
                                call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Quotes aggregated by revenue bracket."""
    auth = await rt.context(call_ctx)
    return await rt.client.get(f"quotes/by-revenue{_qs(startDate=start_date, endDate=end_date)}", auth=auth)


async def get_quote_metrics_report(rt: Runtime, start_date: str = "", end_date: str = "",
                                   min_total: str = "", max_total: str = "",
                                   robot_master_id: str = "", customer_id: str = "",
                                   status_id: str = "", columns: str = "",
                                   call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Filtered quote metrics report rows."""
    auth = await rt.context(call_ctx)
    q = _qs(startDate=start_date, endDate=end_date, minTotal=min_total, maxTotal=max_total,
            robotMasterId=robot_master_id, customerId=customer_id, statusId=status_id,
            columns=columns)
    return await rt.client.get(f"quotes/report{q}", auth=auth)


async def list_quote_metric_details(rt: Runtime, start_date: str = "", end_date: str = "",
                                    status: str = "", revenue_bracket: str = "",
                                    page: str = "1", size: str = "50",
                                    call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Paginated quote drill-down rows for the metrics dashboard."""
    auth = await rt.context(call_ctx)
    q = _qs(startDate=start_date, endDate=end_date, status=status,
            revenueBracket=revenue_bracket, page=page, size=size)
    return await rt.client.get(f"quotes/details{q}", auth=auth)
