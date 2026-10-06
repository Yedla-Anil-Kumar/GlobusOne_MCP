"""Globus One HTTP client.

Two responsibilities:
1. Talk to Globus One — either real HTTP (``live``) or HAR-derived fixtures
   (``fixtures``, default for offline dev).
2. Unwrap the uniform response envelope so tools get clean data.

Every Globus One response looks like::

    { "strStatus": "success" | "error", "strMessage": "...",
      "lstDefault" | "lstXxx": [...] | "objResult": {...} | "strResult": "<json-string>" }

``strResult`` is sometimes a JSON *string* (e.g. release-for-sale) — we double
-parse. A non-"success" ``strStatus`` is raised as :class:`GlobusApiError`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from .auth.base import AuthContext
from .config import settings


class GlobusApiError(RuntimeError):
    """Raised when Globus One returns a non-success envelope or transport fails."""


# Maps an API path (with numeric ids stripped) to its fixture file. Lets the
# fixtures backend answer the same calls the live backend would.
_FIXTURE_MAP: dict[str, str] = {
    "loadUserInfo": "loadUserInfo.json",
    "loadCompany": "loadCompany.json",
    "loadCompanyInfo": "loadCompanyInfo.json",
    "loadCodeLookup": "loadCodeLookup_CRMSTG.json",
    "crm/opp/loadOpportunityDashboard": "loadOpportunityDashboard.json",
    "crm/opp/loadOpportunityStats": "loadOpportunityStats.json",
    "crm/opp/loadOpportunityFilterOptions": "loadOpportunityFilterOptions.json",
    "crm/opp/getOpportunityDetail": "getOpportunityDetail.json",
    "crm/opp/support/loadProducts": "loadProducts.json",
    "crm/opp/support/loadOppQuotes": "loadOppQuotes.json",
    "crm/activity/loadActivities": "loadActivities.json",
    "crm/contact/loadContacts": "loadContacts.json",
    "crm/note/loadNotes": "loadNotes.json",
    "crm/opp/support/loadChangeHistory": "loadChangeHistory.json",
    "loadQuoteDashboardDtls": "loadQuoteDashboardDtls.json",
    "loadReleaseForSaleDtls": "loadReleaseForSaleDtls.json",
}


def _match_fixture(path: str) -> Optional[str]:
    """Find the fixture for an API path by longest matching prefix.

    Handles both numeric (``getOpportunityDetail/272``) and string
    (``loadUserInfo/jdoe``) path params, and ignores query strings.
    """
    clean = path.split("?", 1)[0].strip("/")
    best_key = ""
    for key in _FIXTURE_MAP:
        if (clean == key or clean.startswith(key + "/")) and len(key) > len(best_key):
            best_key = key
    return _FIXTURE_MAP.get(best_key) if best_key else None


class GlobusClient:
    """Async-ish client (methods are async for a uniform live/fixtures API)."""

    def __init__(self, fixtures_dir: Optional[Path] = None) -> None:
        self._fixtures_dir = Path(fixtures_dir or settings.fixtures_dir)
        self._http = None  # lazily created httpx.AsyncClient in live mode

    # -- envelope handling ------------------------------------------------
    @staticmethod
    def unwrap(envelope: dict[str, Any]) -> Any:
        """Return the payload from a Globus One envelope, or raise on error."""
        # getDivision / getZones / getRegions answer with a bare JSON array, and
        # checkRobotNameExists with a bare map — no envelope to open.
        if not isinstance(envelope, dict):
            return envelope
        status = envelope.get("strStatus")
        # Compared without case: the controllers' constant is documented as
        # "SUCCESS" while captured responses carry "success".
        if status is not None and str(status).lower() != "success":
            msg = (envelope.get("strMessage") or "").strip()
            low = msg.lower()
            # Checked FIRST: the "no data" substrings below are too loose to
            # separate an auth failure from an empty result — "User session not
            # found" contains "not found", "No data available for your access
            # level" contains "no data".
            if any(k in low for k in
                   ("session", "login", "unauthor", "not authenticated", "forbidden",
                    "access level", "permission", "denied", "expired")):
                raise GlobusApiError(msg or "Globus One authorisation error")
            # GlobusOne reports "no data" cases with an *error* status (e.g.
            # "... List is empty", "no record found"). Surface those as an empty
            # result so read tools return cleanly instead of throwing — the
            # assistant then simply says there's nothing to show.
            if any(k in low for k in
                   ("empty", "no record", "not found", "no data", "does not exist", "no results")):
                return []
            raise GlobusApiError(msg or "Globus One error")
        # Explicit, high-priority keys (order matters: opportunities use lstDefault).
        # A key that is present but null is skipped: every response is the same
        # envelope ({lstDefault, objResult, strResult, …}), and a serializer that
        # writes nulls would otherwise hand back lstDefault=None for an endpoint
        # whose data is in strResult — an empty result from a full response.
        for key in ("lstDefault", "lstCodeLookUp", "lstUserInfo", "lstCompany",
                    "lstCompanyInfo", "lstAccessPermission", "lstRobotQuotationModel"):
            if envelope.get(key) is not None:
                return envelope[key]
        # Generic: the whole portal returns its payload under a `lst*`/`obj*`
        # (list/object) key that varies per endpoint. Return the first such key
        # so any read tool works without enumerating every DTO wrapper name.
        _meta = {"strStatus", "strMessage", "flag"}
        for key, val in envelope.items():
            if key in _meta:
                continue
            if val is not None and (key.startswith("lst") or key.startswith("obj")
                                    or key.startswith("gm")):
                return val
        if envelope.get("objResult") is not None:
            return envelope["objResult"]
        if envelope.get("strResult") is not None:  # sometimes a JSON-encoded string
            raw = envelope["strResult"]
            try:
                return json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                return raw
        # The last three data keys, from the controllers' setters: an object
        # (order summary, shipping modes, forex values), a number (quote-id
        # match count, a quote's company id) and a boolean (forex-rate validity).
        # `flag` rides along on other envelopes too, so it is the data only when
        # nothing else is — it used to be discarded as a status field, and
        # validateForexRate always came back empty.
        if envelope.get("hashMap") is not None:
            return envelope["hashMap"]
        if envelope.get("intResult") is not None:
            return envelope["intResult"]
        if envelope.get("flag") is not None:
            return envelope["flag"]
        # No known data key — return the envelope minus status fields.
        return {k: v for k, v in envelope.items() if k not in _meta}

    # -- transport --------------------------------------------------------
    async def _raw(self, method: str, path: str, *, auth: AuthContext,
                   json_body: Optional[dict] = None) -> dict[str, Any]:
        if settings.is_live:
            return await self._raw_live(method, path, auth=auth, json_body=json_body)
        return self._raw_fixture(path)

    def _raw_fixture(self, path: str) -> dict[str, Any]:
        fname = _match_fixture(path)
        if not fname:
            raise GlobusApiError(f"No fixture for path '{path}'")
        fpath = self._fixtures_dir / fname
        if not fpath.is_file():
            raise GlobusApiError(f"Fixture file missing: {fpath}")
        return json.loads(fpath.read_text())

    async def _raw_live(self, method: str, path: str, *, auth: AuthContext,
                        json_body: Optional[dict]) -> dict[str, Any]:
        import httpx  # local import; only needed in live mode

        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=settings.api_base,
                timeout=httpx.Timeout(settings.read_timeout,
                                      connect=settings.connect_timeout),
            )
        try:
            resp = await self._http.request(
                method, path, json=json_body,
                headers=dict(auth.headers), cookies=dict(auth.cookies),
            )
            resp.raise_for_status()
            # The AsyncClient is process-wide and httpx merges its cookie jar
            # into every outgoing request, so a cookie set on one user's response
            # would ride along on the next user's call. Per-request cookies
            # passed above are unaffected.
            self._http.cookies.clear()
            return resp.json()
        except Exception as exc:  # noqa: BLE001 — map all transport errors
            raise GlobusApiError(f"Globus One request failed: {exc}") from exc

    # -- public verbs (return unwrapped payloads) -------------------------
    async def get(self, path: str, *, auth: AuthContext) -> Any:
        return self.unwrap(await self._raw("GET", path, auth=auth))

    async def post(self, path: str, body: Any, *, auth: AuthContext) -> Any:
        # Globus One expects identity params echoed in the body. A few endpoints
        # take a JSON *array* body (e.g. template lookups) — send those as-is.
        if isinstance(body, list):
            payload: Any = body
        else:
            payload = {**auth.identity.as_body_params(crm=path.startswith("crm/")), **body}
            _log_scope(path, payload)
        return self.unwrap(await self._raw("POST", path, auth=auth, json_body=payload))

    async def post_envelope(self, path: str, body: dict, *, auth: AuthContext) -> dict[str, Any]:
        """POST returning the full envelope (for calls whose total/meta lives in
        ``strMessage`` — e.g. the opportunity dashboard's row count).

        Returned whole, but still status-checked: callers read `lstDefault` with
        `or []`, so an unexamined error envelope becomes an empty result set.
        """
        merged = {**auth.identity.as_body_params(crm=path.startswith("crm/")), **body}
        _log_scope(path, merged)
        envelope = await self._raw("POST", path, auth=auth, json_body=merged)
        status = envelope.get("strStatus") if isinstance(envelope, dict) else None
        if status is not None and str(status).lower() != "success":
            # Same rules as everywhere else: auth failures raise, "no rows" don't.
            self.unwrap(envelope)
        return envelope

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


def _log_scope(path: str, body: dict) -> None:
    """What decides which rows come back: the companies, and the territory filter
    (dept "S" with admin "N" applies it)."""
    logger.info(f"[scope] {path} user={body.get('strUserId')} "
                f"company={body.get('strCompanyId')} dept={body.get('strDeptName')} "
                f"admin={body.get('strUserAdminAccessFl')}")
