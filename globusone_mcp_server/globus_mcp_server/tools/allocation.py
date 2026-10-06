"""Set-allocation, part/set validation, and quote-build helper read tools.

Covers GmSetAllocationController, GmPartSetValidationController, and the read
endpoints of GmCreateRobotQuoteTransController (forex / distributor / export).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from . import PORTAL_DATE_FORMAT, Runtime


# -- set allocation -------------------------------------------------------
async def get_set_allocation(rt: Runtime, quote_id: str,
                             call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Set-allocation details for a quote — each set, its allocation and status.
    This is what the Set Allocation page shows, confirmation included."""
    auth = await rt.context(call_ctx)
    # Exactly what the screen sends. Empty strSetId/strPlantId went out too, and
    # the screen never sends them.
    return await rt.client.post(
        "loadSetAllocationDetails",
        {"strQuoteId": quote_id, "strDtFormat": PORTAL_DATE_FORMAT}, auth=auth)


async def get_set_allocation_confirmation(rt: Runtime, quote_id: str,
                                          call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """A raw confirmation value from fetchConfirmationFlag, which no portal screen
    calls. Whether a quote's set allocation is confirmed — and by whom, when — is
    on the quote header (get_quote_header); prefer that."""
    auth = await rt.context(call_ctx)
    flag = await rt.client.post("fetchConfirmationFlag", {"strQuoteId": quote_id}, auth=auth)
    # strResult carries a bare flag ("Y"/"N"). Returned as is, it reached the
    # backend as text it could not read, and the model as a letter to interpret.
    if isinstance(flag, str) and flag.strip().upper() in ("Y", "N"):
        return {"quoteId": quote_id, "confirmed": flag.strip().upper() == "Y",
                "flag": flag.strip().upper(),
                "note": "Confirm against the quote header's confirmed by / date."}
    # Live, one quote answered 6143.2782 — a part number, not a flag. Returned
    # with that said, so the model does not read meaning into it.
    return {"quoteId": quote_id, "value": flag,
            "note": ("This endpoint did not return a confirmation flag. Whether set "
                     "allocation is confirmed is on the quote header "
                     "(get_quote_header: confirmed by, confirmed date) — use that.")}


async def get_quote_set_details(rt: Runtime, quote_id: str, set_id: str = "", cn_id: str = "",
                                call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Quote set details view."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "viewQuoteSetDetails",
        {"strQuoteId": quote_id, "strSetId": set_id, "strCNId": cn_id}, auth=auth)


# -- part / set validation ------------------------------------------------
async def get_part_details(rt: Runtime, part_number: str,
                           call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Part master details (validation) for a part number."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("fetchPartDeatils", {"partNumberId": part_number}, auth=auth)


async def get_set_details(rt: Runtime, set_id: str,
                          call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Set master details (validation) for a set id."""
    auth = await rt.context(call_ctx)
    # The screen sends `setId`; GmSetMasterModel declares `setDesc` and no setId.
    # Both carry the same value until a recording shows which one the server reads.
    return await rt.client.post("fetchSetDeatils", {"setId": set_id, "setDesc": set_id}, auth=auth)


# -- quote-build helpers (reads) ------------------------------------------
async def get_distributor_type(rt: Runtime, rsm_id: str,
                               call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Distributor type for an RSM."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("fetchDistributorType", {"strRsmId": rsm_id}, auth=auth)


async def get_forex_dropdown_values(rt: Runtime, quote_id: str = "", company_id: str = "",
                                    call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Forex dropdown values for a quote."""
    auth = await rt.context(call_ctx)
    return await rt.client.post(
        "loadForexDropDownValues", {"strQuoteId": quote_id, "strCompanyId": company_id}, auth=auth)


async def validate_forex_rate(rt: Runtime, company_id: str = "",
                              call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Validate a company's forex rate (pure validation)."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("validateForexRate", {"companyId": company_id}, auth=auth)


async def get_set_part_export(rt: Runtime, quote_id: str,
                              call_ctx: Optional[Mapping[str, Any]] = None) -> Any:
    """Excel export of set/part details for a quote."""
    auth = await rt.context(call_ctx)
    return await rt.client.post("exportExcel", {"strQuoteId": quote_id}, auth=auth)
