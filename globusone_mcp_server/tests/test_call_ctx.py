"""The per-request token actually reaches the auth layer (NEU-976).

The gap this covers: Corporate GPT forwarded the end user's JWT as
``Authorization: Bearer <jwt>`` on the MCP connection, but nothing on this side
read it. Every tool is registered either through ``_register_module``, whose
wrapper calls ``fn(_rt, *args, **kwargs)``, or by hand as
``ctx_tools.get_user_context(_rt, login)`` — neither forwards ``call_ctx``. So
``PassthroughAuth.prepare`` always received ``None``, derived an empty Identity,
and every tool returned no data.

Silently, which is what made it dangerous: an empty Identity fails closed rather
than raising, so switching GLOBUS_AUTH_MODE to passthrough would have turned the
assistant off with no error anywhere.

Run: .venv/bin/python globus_mcp_server/tests/test_call_ctx.py
"""

from __future__ import annotations

import asyncio
import datetime as _dt

import jwt

from globus_mcp_server.auth.base import AuthContext, Identity
from globus_mcp_server.auth.passthrough import PassthroughAuth
from globus_mcp_server.tools import Runtime

SECRET = "test-shared-secret"
AUD = "corporate-gpt-widget"
ISS = "globusone"


def _mint(**overrides) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    claims = {
        "iss": ISS, "aud": AUD, "sub": "1000001",
        "party_id": "2000002", "access_level": "3",
        "company_id": "100", "dept_name": "Sales", "user_admin_access_fl": "N",
        "exp": now + _dt.timedelta(minutes=5),
        **overrides,
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")


class _RecordingAuth:
    """Captures what the Runtime hands the auth strategy."""

    def __init__(self):
        self.seen = "unset"

    async def prepare(self, call_ctx=None):
        self.seen = call_ctx
        return AuthContext(headers={}, cookies={}, identity=Identity())


def _runtime(auth, provider=None) -> Runtime:
    return Runtime(client=None, codes=None, auth=auth, call_ctx_provider=provider)


# ── Runtime resolves the ambient context ───────────────────────────────────

def test_the_provider_supplies_the_context_when_a_tool_passes_none():
    """The whole bug: tools pass nothing, so something must fill it in."""
    auth = _RecordingAuth()
    rt = _runtime(auth, provider=lambda: {"token": "abc"})
    asyncio.run(rt.context())
    assert auth.seen == {"token": "abc"}


def test_an_explicit_context_wins_over_the_provider():
    """Tests and fixture runs pass `call_ctx` directly and must keep control."""
    auth = _RecordingAuth()
    rt = _runtime(auth, provider=lambda: {"token": "ambient"})
    asyncio.run(rt.context({"token": "explicit"}))
    assert auth.seen == {"token": "explicit"}


def test_without_a_provider_nothing_changes():
    """Unit tests construct Runtime with three fields; that must still behave
    exactly as before this was added."""
    auth = _RecordingAuth()
    asyncio.run(_runtime(auth).context())
    assert auth.seen is None


def test_an_empty_context_is_still_passed_through():
    """`{}` from the provider means "a request, but no usable token" — distinct
    from "no provider", and it must reach the auth layer to fail closed there."""
    auth = _RecordingAuth()
    asyncio.run(_runtime(auth, provider=dict).context())
    assert auth.seen == {}


# ── the header reader ──────────────────────────────────────────────────────

def _call_ctx_with_headers(headers):
    """Drive `_request_call_ctx` against a faked inbound request."""
    from globus_mcp_server import server

    class _Req:
        def __init__(self, h): self.headers = h
    class _ReqCtx:
        def __init__(self, h): self.request = _Req(h)
    class _Ctx:
        def __init__(self, h): self.request_context = _ReqCtx(h)

    original = server.mcp.get_context
    server.mcp.get_context = lambda: _Ctx(headers)
    try:
        return server._request_call_ctx()
    finally:
        server.mcp.get_context = original


def test_a_bearer_token_is_extracted():
    assert _call_ctx_with_headers({"authorization": "Bearer eyJabc.def.ghi"}) \
        == {"token": "eyJabc.def.ghi"}


def test_the_scheme_is_matched_case_insensitively():
    """Okta and most clients send "Bearer"; some send "bearer"."""
    assert _call_ctx_with_headers({"authorization": "bearer tok"}) == {"token": "tok"}


def test_a_missing_header_yields_no_token():
    assert _call_ctx_with_headers({}) == {}


def test_a_non_bearer_scheme_is_ignored():
    """Basic auth must not be forwarded as though it were a user token."""
    assert _call_ctx_with_headers({"authorization": "Basic dXNlcjpwYXNz"}) == {}


def test_an_empty_bearer_yields_no_token():
    assert _call_ctx_with_headers({"authorization": "Bearer   "}) == {}


def test_outside_a_request_it_returns_empty_rather_than_raising():
    """`get_context()` raises when there is no active request. A counter or a
    warmup call must not crash on that."""
    from globus_mcp_server import server
    assert server._request_call_ctx() == {}


# ── end to end: token in a header becomes the acting Identity ──────────────

def test_a_token_on_the_request_becomes_the_acting_identity():
    """The proof. A JWT arriving as a Bearer header ends up as the Identity that
    gets echoed into Globus One calls — which is what was silently failing."""
    auth = PassthroughAuth(secret=SECRET, audience=AUD, issuer=ISS)
    rt = _runtime(auth, provider=lambda: {"token": _mint()})

    ctx = asyncio.run(rt.context())

    assert ctx.identity.user_id == "1000001"
    assert ctx.identity.party_id == "2000002"
    assert ctx.identity.access_level == "3"
    assert ctx.headers.get("Authorization", "").startswith("Bearer ")
    # The six values Globus One expects echoed in every POST body.
    body = ctx.identity.as_body_params()
    assert body["strUserId"] == "1000001"
    assert body["strPartyId"] == "2000002"
    assert body["strAccessLevel"] == "3"


def test_a_bad_token_yields_no_identity_rather_than_someone_elses():
    """Fails closed: an expired token must not silently inherit whatever the
    service account could see."""
    auth = PassthroughAuth(secret=SECRET, audience=AUD, issuer=ISS)
    expired = _mint(exp=_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=1))
    ctx = asyncio.run(_runtime(auth, provider=lambda: {"token": expired}).context())

    assert ctx.identity.user_id == ""
    assert ctx.identity.as_body_params()["strUserId"] == ""


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all call_ctx tests passed")
