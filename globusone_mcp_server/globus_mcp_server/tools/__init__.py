"""Tool implementations for the Globus One MCP server.

Each module exposes plain async functions taking a shared :class:`Runtime`
(client + code resolver + auth) plus typed args, and returning clean dicts.
``server.py`` wraps them as MCP tools. Keeping them framework-free makes them
directly unit-testable against fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from ..auth.base import AuthContext, AuthStrategy

# The `strDtFormat` the portal's own screens send, which it takes from
# loadCompanyInfo's `dtFormat` ("MM/dd/yyyy" for Globus Medical North America).
PORTAL_DATE_FORMAT = "MM/dd/yyyy"


def header_body(quote_id: str) -> dict:
    """The quote-header request as the portal's shared header call sends it.

    With the date format: the header's dates (confirmed date among them) are
    formatted with it, and without it they came back empty — "Confirmed date:
    not recorded" for a quote the portal shows confirmed on 10/29/2025.
    """
    return {"strQuoteId": quote_id, "strDtFormat": PORTAL_DATE_FORMAT}


def header_row(header: Any) -> dict:
    """The one record loadQuoteHeaderDetails returns, whether or not it is listed."""
    row = header[0] if isinstance(header, list) and header else header
    return row if isinstance(row, dict) else {}
from ..client import GlobusClient
from ..codes import CodeResolver


@dataclass
class Runtime:
    client: GlobusClient
    codes: CodeResolver
    auth: AuthStrategy
    # Supplies the auth context for calls that do not pass one explicitly.
    # ``server.py`` sets this to a reader for the inbound request's Bearer token.
    # Left unset in tests and fixture runs, which pass ``call_ctx`` directly.
    call_ctx_provider: Optional[Callable[[], Mapping[str, Any]]] = None

    async def context(self, call_ctx: Optional[Mapping[str, Any]] = None) -> AuthContext:
        """Resolve the auth context for one outbound Globus One call.

        Every tool funnels through here: :class:`GlobusClient` takes ``auth`` as a
        required keyword on every method, so no request reaches the CRM without an
        AuthContext. That makes this the single place the per-request token can be
        picked up — which is why it is done here rather than in the MCP wrappers.

        It had to be somewhere. The wrappers in ``server.py`` invoke each tool as
        ``fn(_rt, *args, **kwargs)`` and never forward ``call_ctx``, and fifteen
        more tools are registered by hand with the same omission. So under
        passthrough auth every call arrived with no token, ``PassthroughAuth``
        derived an empty Identity, and every tool returned no data — silently,
        because an empty Identity fails closed rather than raising.

        An explicit ``call_ctx`` always wins, so tests keep full control and the
        provider is only consulted when a caller supplied nothing.
        """
        if call_ctx is None and self.call_ctx_provider is not None:
            call_ctx = self.call_ctx_provider()
        return await self.auth.prepare(call_ctx)
