"""Auth seam for outbound Globus One calls.

Globus One authenticates via a Java ``JSESSIONID`` cookie set after Okta SSO.
That cookie is HttpOnly (the widget JS can't read it) and the MCP server runs
server-side, so *how* the end user's identity reaches Globus One is an open
decision (NEU-976). This module defines the interface so tools never touch auth
directly; the concrete strategy is chosen at startup by ``GLOBUS_AUTH_MODE``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class Identity:
    """The acting user, as Globus One identifies them (from ``loadUserInfo``)."""

    user_id: str = ""
    party_id: str = ""
    access_level: str = ""
    company_id: str = ""
    dept_name: str = ""
    # "Y" lifts the territory (V700) filter for a Sales user. The portal sets it
    # from security events, not from loadUserInfo: the quote screens from
    # INR_QUOTE_DASH_ADMIN, the CRM screens from that or INR_QUOTE_ADMIN.
    user_admin_access_fl: str = "N"
    crm_admin_access_fl: str = "N"

    def as_body_params(self, crm: bool = False) -> dict[str, str]:
        """Identity fields Globus One expects echoed in POST bodies."""
        return {
            "strUserId": self.user_id,
            "strPartyId": self.party_id,
            "strAccessLevel": self.access_level,
            "strCompanyId": self.company_id,
            "strDeptName": self.dept_name,
            "strUserAdminAccessFl": self.crm_admin_access_fl if crm else self.user_admin_access_fl,
        }


@dataclass(frozen=True)
class AuthContext:
    """Everything the HTTP client applies to an authenticated request."""

    headers: Mapping[str, str] = field(default_factory=dict)
    cookies: Mapping[str, str] = field(default_factory=dict)
    identity: Identity = field(default_factory=Identity)


@runtime_checkable
class AuthStrategy(Protocol):
    """Resolve an :class:`AuthContext` for one inbound MCP call.

    ``call_ctx`` carries whatever the MCP client forwarded — e.g. a passthrough
    token and/or a resolved identity. Implementations must be per-request safe.
    """

    async def prepare(self, call_ctx: Optional[Mapping[str, Any]] = None) -> AuthContext:
        ...


def _identity_from(call_ctx: Optional[Mapping[str, Any]]) -> Identity:
    """Best-effort Identity from a call context (accepts Identity or dict)."""
    if not call_ctx:
        return Identity()
    ident = call_ctx.get("identity")
    if isinstance(ident, Identity):
        return ident
    if isinstance(ident, Mapping):
        allowed = Identity.__dataclass_fields__.keys()
        return Identity(**{k: str(v) for k, v in ident.items() if k in allowed})
    return Identity()
