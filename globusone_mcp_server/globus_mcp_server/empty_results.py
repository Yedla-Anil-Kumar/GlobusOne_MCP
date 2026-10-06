"""An empty answer about one quote must not read as a fact about that quote.

Live, on Order Process: "What's the order process status for quote
GMQT-101725-3?" The model called `get_order_summary`, which returned `[]`, and
answered "All orders for quote GMQT-101725-3 are Shipped" from rows fetched
earlier in the conversation. The portal's header said "Order Process
In-Progress", with one set still in Set Allocation. The same bare `[]` had
already produced "set allocation has not been confirmed" on Set Allocation.

The GlobusOne endpoints return `[]` for "nothing recorded yet", for "no such
quote", and for "wrong parameters for this endpoint" alike (see
`GlobusClient.unwrap`). A small model then fills that gap from whatever it
can find. This does for every per-quote tool what `_sub` does for the
opportunity sub-lists: an empty result is checked against the quote header,
and comes back as either "not visible to you" or "exists — here is its
status, and this endpoint says nothing more". It costs one extra call, and
only when there was nothing to show anyway.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

# Tools that ARE the existence check. An empty answer from one of them means the
# quote is not visible, with no second call needed.
EXISTENCE_TOOLS = frozenset({"get_quote_header", "validate_quote_id"})


def is_empty(result: Any) -> bool:
    return result is None or result == [] or result == {} or result == ""


def header_status(header: Any) -> str:
    """The quote's workflow status as its header shows it ("Order Process In-Progress").

    The header's exact field name is not documented. The dashboard rows carry the
    name in `strQuoteStatusNm` and the id in `strQuoteStatusId`, so the name is
    preferred, and a bare number (an id) is never returned as a status.
    """
    row = header[0] if isinstance(header, list) and header else header
    if not isinstance(row, dict):
        return ""
    candidates = [(k, v) for k, v in row.items()
                  if "status" in k.lower() and isinstance(v, str) and v.strip()
                  and not v.strip().isdigit()]
    for key, value in candidates:
        if key.lower().endswith(("statusnm", "statusname")):
            return value.strip()
    return candidates[0][1].strip() if candidates else ""


def quote_not_found(quote_id: str) -> dict[str, Any]:
    return {
        "nothingReturned": True,
        "found": False,
        "quoteId": quote_id,
        "caveat": (
            f"No quote {quote_id} is visible to you. It may not exist, or it may "
            "sit outside what your account can see. Tell the user exactly that, "
            "and do not describe its status or orders from anything seen earlier."
        ),
    }


async def explain_empty(tool_name: str, quote_id: str, result: Any,
                        fetch_header: Callable[[str], Awaitable[Any]]) -> Any:
    """Pass a non-empty result through untouched; explain an empty one."""
    if not quote_id or not is_empty(result):
        return result
    if tool_name in EXISTENCE_TOOLS:
        return quote_not_found(quote_id)
    try:
        header = await fetch_header(quote_id)
    except Exception:  # noqa: BLE001 — the check must not replace the answer
        header = None
        checked = False
    else:
        checked = True
    if checked and is_empty(header):
        return quote_not_found(quote_id)

    # `nothingReturned` is for the backend, not the model: a turn whose lookups
    # all came back like this has its answer written from this payload, because
    # the model ignored the caveat and repeated an earlier answer instead.
    out: dict[str, Any] = {"nothingReturned": True, "quoteId": quote_id, "results": []}
    status = header_status(header) if checked else ""
    if status:
        out["found"] = True
        out["quoteStatus"] = status
    caveat = (
        f"`{tool_name}` returned nothing for quote {quote_id}. That is NOT "
        "evidence about the quote: do not report it as having no orders, as "
        "not confirmed, not shipped or not allocated. "
    )
    caveat += (f"The quote's current status is \"{status}\" — answer status "
               "questions with that. " if status else
               "Nothing fetched here shows its status: say so rather than "
               "guessing one. ")
    caveat += ("Answer only from results fetched in THIS turn: data from earlier "
               "in the conversation may be stale, or about another quote.")
    out["caveat"] = caveat
    return out
