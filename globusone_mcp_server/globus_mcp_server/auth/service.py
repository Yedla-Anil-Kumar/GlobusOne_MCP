"""Option B — service-account auth.

The MCP server authenticates to Globus One with its own credentials and (once
the login flow is captured) caches the resulting ``JSESSIONID``. The acting
end user is represented by identity params forwarded on the call context. In
``fixtures`` mode there is nothing to log into.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .base import AuthContext, Identity, _identity_from


class ServiceAccountAuth:
    def __init__(
        self,
        *,
        live: bool,
        user: str = "",
        secret: str = "",
        session_cookie: str = "",
        identity: Optional[Identity] = None,
    ) -> None:
        self._live = live
        self._user = user
        self._secret = secret
        # DEMO: a borrowed JSESSIONID + its matching identity, injected on every
        # call so live mode can hit the running test portal without the Okta flow.
        self._cookies: dict[str, str] = {"JSESSIONID": session_cookie} if session_cookie else {}
        self._identity = identity

    async def _ensure_session(self) -> None:
        # TODO(live/NEU-976): perform the real service login and cache JSESSIONID.
        # The demo path injects a borrowed cookie via the constructor instead.
        if not self._live:
            return

    async def prepare(self, call_ctx: Optional[Mapping[str, Any]] = None) -> AuthContext:
        await self._ensure_session()
        identity = self._identity or _identity_from(call_ctx)
        return AuthContext(
            headers={},
            cookies=dict(self._cookies),
            identity=identity,
        )
