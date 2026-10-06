"""Pluggable auth for outbound Globus One calls (see ``base.py``)."""

from __future__ import annotations

from ..config import settings
from .base import AuthContext, AuthStrategy, Identity
from .passthrough import PassthroughAuth
from .service import ServiceAccountAuth


def build_auth_strategy() -> AuthStrategy:
    """Select the strategy from ``GLOBUS_AUTH_MODE`` (NEU-976 pending)."""
    if settings.auth_mode == "passthrough":
        return PassthroughAuth(
            secret=settings.passthrough_secret,
            okta_issuer=settings.okta_issuer,
            audience=settings.okta_audience,
        )
    # DEMO: if a borrowed session cookie is configured, inject it + the matching
    # identity so live mode can hit the running test portal without the Okta flow.
    demo_identity = None
    if settings.demo_user_id:
        demo_identity = Identity(
            user_id=settings.demo_user_id,
            party_id=settings.demo_party_id,
            access_level=settings.demo_access_level,
            dept_name=settings.demo_dept_name,
            user_admin_access_fl=settings.demo_admin_fl,
        )
    return ServiceAccountAuth(
        live=settings.is_live,
        user=settings.service_user,
        secret=settings.service_secret,
        session_cookie=settings.session_cookie,
        identity=demo_identity,
    )


__all__ = [
    "AuthContext",
    "AuthStrategy",
    "Identity",
    "PassthroughAuth",
    "ServiceAccountAuth",
    "build_auth_strategy",
]
