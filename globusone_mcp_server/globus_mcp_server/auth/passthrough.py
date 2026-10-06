"""Option A (chosen) — delegated user-token passthrough.

The GlobusOne backend mints a short-lived signed JWT for the signed-in user; the
widget forwards it through Corporate GPT to this server (as ``call_ctx["token"]``
/ the Bearer header). We verify it against the shared secret, derive the acting
:class:`Identity` from its claims, and forward it to GlobusOne — preserving the
end user's own permissions end to end.

Token contract (see docs/globus_widget/AUTH_PASSTHROUGH.md §3). Claims consumed:
``sub``/``end_user_ref`` → user id, ``party_id``, ``access_level``, plus standard
``exp``/``aud``/``iss``. HS256 with a shared secret is the default; the claim
names are overridable so we can match GlobusOne's exact mint without a code
change. Verification is strict but *fails closed to an empty Identity* rather
than raising, so a bad/absent token yields no data instead of a 500.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

import jwt

from .base import AuthContext, Identity, _identity_from
from .user_info import resolve_identity, with_admin_flags

# Expected audience/issuer for our tokens (overridable via constructor).
_DEFAULT_AUDIENCE = "corporate-gpt-widget"
_DEFAULT_ISSUER = "globusone"


class PassthroughAuth:
    def __init__(
        self,
        *,
        secret: str = "",
        okta_issuer: str = "",
        audience: str = _DEFAULT_AUDIENCE,
        issuer: str = _DEFAULT_ISSUER,
        algorithms: Optional[list[str]] = None,
        verify_aud: bool = True,
        verify_iss: bool = True,
    ) -> None:
        self._secret = secret
        self._okta_issuer = (okta_issuer or "").rstrip("/")
        self._audience = audience
        # When an Okta issuer is configured, tokens are issued *by Okta*, so the
        # expected issuer claim is the Okta issuer itself.
        self._issuer = self._okta_issuer or issuer
        self._algorithms = algorithms or (["RS256"] if self._okta_issuer else ["HS256"])
        self._verify_aud = verify_aud
        self._verify_iss = verify_iss
        self._jwks_client = None  # lazily created PyJWKClient (RS256/Okta path)

    def _signing_key(self, token: str):
        """Fetch the RS256 signing key for this token from Okta's JWKS."""
        if self._jwks_client is None:
            from jwt import PyJWKClient
            self._jwks_client = PyJWKClient(f"{self._okta_issuer}/v1/keys")
        return self._jwks_client.get_signing_key_from_jwt(token).key

    def _verify(self, token: str) -> Identity:
        """Verify the signed token and derive the acting Identity.

        Uses Okta JWKS (RS256) when an Okta issuer is configured, else an HS256
        shared secret (dev). Returns an empty Identity on any failure (bad
        signature, expired, wrong aud/iss, malformed) — the caller returns no data.
        """
        if not token or not (self._okta_issuer or self._secret):
            return Identity()
        try:
            key = self._signing_key(token) if self._okta_issuer else self._secret
            claims = jwt.decode(
                token,
                key,
                algorithms=self._algorithms,
                audience=self._audience if self._verify_aud else None,
                issuer=self._issuer if self._verify_iss else None,
                options={
                    "require": ["exp"],
                    "verify_aud": self._verify_aud,
                    "verify_iss": self._verify_iss,
                },
            )
        except Exception:  # noqa: BLE001 — JWKS/network/JWT errors all fail closed
            return Identity()

        user_id = str(claims.get("sub") or claims.get("end_user_ref") or "")
        if not user_id:
            return Identity()
        return Identity(
            user_id=user_id,
            party_id=str(claims.get("party_id") or ""),
            access_level=str(claims.get("access_level") or ""),
            company_id=str(claims.get("company_id") or ""),
            dept_name=str(claims.get("dept_name") or ""),
            user_admin_access_fl=str(claims.get("user_admin_access_fl") or "N"),
        )

    async def prepare(self, call_ctx: Optional[Mapping[str, Any]] = None) -> AuthContext:
        ctx = dict(call_ctx or {})
        token = str(ctx.get("token") or "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # Verified claims are authoritative; fall back to a forwarded identity
        # only when no verifier is configured (dev / fixtures).
        if token and (self._okta_issuer or self._secret):
            identity = self._verify(token)
            identity = await self._complete_identity(token, identity)
        else:
            identity = _identity_from(ctx)
        return AuthContext(headers=headers, cookies={}, identity=identity)

    async def _complete_identity(self, token: str, identity: Identity) -> Identity:
        """Fill in what the token proves but does not carry.

        Okta's token establishes WHO the user is; the five values that decide what
        they may SEE live in Globus One's own tables. When the claims already carry
        them — which they would if Okta is ever configured with custom claims —
        only the admin flags are looked up.

        Resolved from the VERIFIED subject, never from the call context: the portal
        keeps these values in browser localStorage and posts them back, so anything
        caller-supplied could be edited to raise the sender's own access level.
        """
        if identity.party_id and identity.access_level:
            # The admin flags are never a claim; the portal reads them from
            # security events, so they are read here either way.
            return await with_admin_flags(token, identity)
        if not identity.user_id:
            return identity          # verification failed; stay failed-closed
        return await resolve_identity(token, identity.user_id)
