"""Resolving the acting user's Identity from loadUserInfo (NEU-976).

Built against a real response captured from erptest3 on 2026-09-15. Okta's token
carries `sub` and `groups` and none of the five values that decide what a user may
see, so a verified token alone makes every Globus One call return nothing — the
API wants those values echoed in each request body.

The fixture below is that live response with its values partially redacted. The
three things it taught us, each of which would have been a silent bug:

  * `strCompanyId` comes back as an **integer** while every sibling is a string.
  * `strUserAdminAccessFl` is **not returned at all**.
  * `strAccessLevel` is **"1"** for a real user — we had been running at "3" on a
    borrowed service session, so results will legitimately change.

Run: .venv/bin/python globus_mcp_server/tests/test_user_info.py
"""

from __future__ import annotations

import asyncio

from globus_mcp_server.auth.base import Identity
from globus_mcp_server.auth.user_info import (
    clear_cache,
    identity_from_user_info,
    resolve_identity,
    username_from_subject,
    _unwrap_user_info,
)

# The live response, values redacted the way they were shared.
LIVE_RESPONSE = {
    "strStatus": "success",
    "strMessage": "User Info list retrieved successfully",
    "lstUserInfo": [
        {
            "strLoginName": "jdoe",
            "strCompanyId": 1000,          # int, unlike every sibling
            "strPartyId": "2000001",
            "strUserId": "1000001",
            "strUserName": "Jane Doe",
            "strCodeId": None,
            "strPartyType": "7006",
            "strAccessLevel": "1",
            "strDivisionId": None,
            "strDeptName": "S",
            "strCompId": "100800",
        }
    ],
}
RECORD = LIVE_RESPONSE["lstUserInfo"][0]


# ── sub → username ─────────────────────────────────────────────────────────

def test_the_okta_subject_becomes_the_login_name():
    """Okta's `sub` is the full login; the endpoint takes the bare name. The live
    response echoes `strLoginName: "jdoe"`, which is how we know this is right."""
    assert username_from_subject("jdoe@example.com") == "jdoe"


def test_a_bare_login_is_used_as_is():
    assert username_from_subject("jdoe") == "jdoe"


def test_an_empty_subject_yields_nothing_to_look_up():
    assert username_from_subject("") == ""


# ── the envelope ───────────────────────────────────────────────────────────

def test_the_record_is_unwrapped_from_the_standard_envelope():
    """Same `{strStatus, strMessage, lst*}` shape as every other Globus One call."""
    assert _unwrap_user_info(LIVE_RESPONSE) == RECORD


def test_an_empty_list_yields_no_record():
    assert _unwrap_user_info({"strStatus": "success", "lstUserInfo": []}) is None


def test_a_malformed_payload_yields_no_record():
    for payload in (None, [], "text", {"lstUserInfo": "nope"}, {}):
        assert _unwrap_user_info(payload) is None


# ── the mapping ────────────────────────────────────────────────────────────

def test_the_live_record_maps_to_a_complete_identity():
    identity = identity_from_user_info(RECORD)
    assert identity.user_id == "1000001"
    assert identity.party_id == "2000001"
    assert identity.access_level == "1"
    assert identity.dept_name == "S"


def test_the_integer_company_id_is_coerced_to_a_string():
    """`strCompanyId` is an int in the response and `str` on Identity. Sending an
    int would mismatch silently — the API would simply return less data."""
    identity = identity_from_user_info(RECORD)
    assert identity.company_id == "1000"
    assert isinstance(identity.company_id, str)


def test_the_missing_admin_flag_defaults_to_no():
    """`strUserAdminAccessFl` is absent from the response. "N" is the safe
    direction — "Y" would assert admin scope nobody told us this user has."""
    assert "strUserAdminAccessFl" not in RECORD
    assert identity_from_user_info(RECORD).user_admin_access_fl == "N"


def test_every_value_globus_one_needs_is_present():
    """The six echoed in each request body. Five come from the response; the sixth
    is the safe default."""
    body = identity_from_user_info(RECORD).as_body_params()
    assert body == {
        "strUserId": "1000001",
        "strPartyId": "2000001",
        "strAccessLevel": "1",
        "strCompanyId": "1000",
        "strDeptName": "S",
        "strUserAdminAccessFl": "N",
    }


def test_null_fields_do_not_overwrite_defaults():
    """`strCodeId` and `strDivisionId` come back as null; a None must not become
    the string "None" anywhere it is sent."""
    record = {**RECORD, "strDeptName": None}
    assert identity_from_user_info(record).dept_name == ""


# ── fetching, caching, and failing closed ──────────────────────────────────

class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Records what would have gone over the wire."""

    calls: list = []

    def __init__(self, payload, status=200):
        self._payload, self._status = payload, status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        type(self).calls.append((url, dict(headers or {})))
        return _FakeResponse(self._payload, self._status)


def _patch_httpx(monkey_payload, status=200):
    """Swap httpx.AsyncClient for the duration of one resolve_identity call."""
    import httpx
    original = httpx.AsyncClient
    _FakeClient.calls = []
    httpx.AsyncClient = lambda **kw: _FakeClient(monkey_payload, status)
    return original


def _restore_httpx(original):
    import httpx
    httpx.AsyncClient = original


def test_the_token_is_sent_as_a_bearer_to_the_right_url():
    """Server-to-server, no cookie: a browser session gets 401 on this endpoint."""
    clear_cache()
    original = _patch_httpx(LIVE_RESPONSE)
    try:
        identity = asyncio.run(resolve_identity("tok123", "jdoe@example.com"))
    finally:
        _restore_httpx(original)

    url, headers = _FakeClient.calls[0]
    assert url.endswith("/loadUserInfo/jdoe")
    assert headers["Authorization"] == "Bearer tok123"
    assert identity.user_id == "1000001"


def test_a_second_lookup_is_served_from_cache():
    """This sits in front of every tool call — an uncached lookup would double the
    round trips for the whole agent."""
    clear_cache()
    original = _patch_httpx(LIVE_RESPONSE)
    try:
        asyncio.run(resolve_identity("tok", "jdoe@example.com"))
        first = len(_FakeClient.calls)
        asyncio.run(resolve_identity("tok", "jdoe@example.com"))
    finally:
        _restore_httpx(original)
    assert len(_FakeClient.calls) == first, "the second lookup made no calls"
    assert sum("/loadUserInfo/" in url for url, _ in _FakeClient.calls) == 1


def test_an_http_failure_fails_closed():
    """No identity means Globus One returns nothing — which is correct. Falling
    back to anything else would serve one user another user's data."""
    clear_cache()
    original = _patch_httpx(LIVE_RESPONSE, status=401)
    try:
        identity = asyncio.run(resolve_identity("tok", "jdoe@example.com"))
    finally:
        _restore_httpx(original)
    assert identity == Identity()
    assert identity.as_body_params()["strUserId"] == ""


def test_an_empty_result_fails_closed_and_is_not_cached():
    clear_cache()
    original = _patch_httpx({"strStatus": "success", "lstUserInfo": []})
    try:
        assert asyncio.run(resolve_identity("tok", "jdoe@example.com")) == Identity()
        asyncio.run(resolve_identity("tok", "jdoe@example.com"))
    finally:
        _restore_httpx(original)
    assert len(_FakeClient.calls) == 2, "a failure must not be cached"


def test_no_token_means_no_lookup():
    clear_cache()
    assert asyncio.run(resolve_identity("", "jdoe@example.com")) == Identity()




# ── the lookup key must come from the VERIFIED token ───────────────────────
# The portal keeps these identity values in browser localStorage and posts them
# back, which means a user can edit their own access level. If we resolved from
# anything the caller sends — or looked anyone up after verification failed — the
# token would be decorative and the old weakness would come straight back.

import datetime as _dt

import jwt as _jwt

from globus_mcp_server.auth.passthrough import PassthroughAuth

_SECRET, _AUD, _ISS = "test-shared-secret", "corporate-gpt-widget", "globusone"


def _auth():
    return PassthroughAuth(secret=_SECRET, audience=_AUD, issuer=_ISS)


def _token(sub="jdoe@example.com", **over):
    now = _dt.datetime.now(_dt.timezone.utc)
    claims = {"iss": _ISS, "aud": _AUD, "sub": sub,
              "exp": now + _dt.timedelta(minutes=5), **over}
    return _jwt.encode(claims, _SECRET, algorithm="HS256")


def test_a_token_that_fails_verification_triggers_no_lookup_at_all():
    """An unverifiable token must not become a lookup for someone. Without this,
    a forged or expired token could still resolve a real user's permissions."""
    clear_cache()
    original = _patch_httpx(LIVE_RESPONSE)
    try:
        ctx = asyncio.run(_auth().prepare({"token": "not-a-jwt"}))
    finally:
        _restore_httpx(original)

    assert _FakeClient.calls == [], "verification failed, yet it went looking anyway"
    assert ctx.identity == Identity()


def test_an_expired_token_triggers_no_lookup():
    clear_cache()
    expired = _token(exp=_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=1))
    original = _patch_httpx(LIVE_RESPONSE)
    try:
        ctx = asyncio.run(_auth().prepare({"token": expired}))
    finally:
        _restore_httpx(original)

    assert _FakeClient.calls == []
    assert ctx.identity.as_body_params()["strUserId"] == ""


def test_the_lookup_uses_the_subject_from_the_token_not_the_call_context():
    """Anything in the call context is caller-supplied. Only the signed `sub`
    decides whose identity we fetch."""
    clear_cache()
    original = _patch_httpx(LIVE_RESPONSE)
    try:
        asyncio.run(_auth().prepare({
            "token": _token(sub="jdoe@example.com"),
            "identity": {"user_id": "9999999", "access_level": "3"},   # ignored
            "sub": "someone.else@example.com",                   # ignored
        }))
    finally:
        _restore_httpx(original)

    url, _ = _FakeClient.calls[0]
    assert url.endswith("/loadUserInfo/jdoe"), f"looked up the wrong user: {url}"


def test_claims_that_already_carry_the_identity_skip_the_lookup():
    """If Okta is ever configured with custom claims, this costs no round trip."""
    clear_cache()
    full = _token(party_id="2000001", access_level="1", company_id="1000", dept_name="S")
    original = _patch_httpx(LIVE_RESPONSE)
    try:
        ctx = asyncio.run(_auth().prepare({"token": full}))
    finally:
        _restore_httpx(original)

    urls = [url for url, _ in _FakeClient.calls]
    assert not any("/loadUserInfo/" in u for u in urls), "claims were sufficient; no lookup needed"
    assert all("/getAccessPermissions/2000001/" in u for u in urls), "only the admin flags"
    assert ctx.identity.access_level == "1"


# ── the admin flags the portal reads from security events ──────────────────
#
# loadUserInfo does not return strUserAdminAccessFl, so it was always "N". For a
# Sales user "N" applies the territory filter, and a dashboard admin on
# erptest3 saw only North American tasks and deals while the portal — which
# reads the flag from getAccessPermissions — showed every region.

class _RoutedClient(_FakeClient):
    """loadUserInfo gets the live record; each security event its own answer."""

    events: dict = {}

    async def get(self, url, headers=None):
        type(self).calls.append((url, dict(headers or {})))
        if "/getAccessPermissions/" in url:
            reply = type(self).events.get(url.rsplit("/", 1)[-1])
            if isinstance(reply, Exception):
                return _FakeResponse({}, status=500)
            return _FakeResponse(reply or {"strStatus": "success", "lstAccessPermission": []})
        return _FakeResponse(self._payload, self._status)


def _resolve_with(events):
    import httpx
    clear_cache()
    original = httpx.AsyncClient
    _RoutedClient.calls, _RoutedClient.events = [], events
    httpx.AsyncClient = lambda **kw: _RoutedClient(LIVE_RESPONSE)
    try:
        return asyncio.run(resolve_identity("tok", "jdoe@example.com"))
    finally:
        httpx.AsyncClient = original
        clear_cache()


def _granted(flag="Y"):
    return {"strStatus": "success", "lstAccessPermission": [{"updateAccessFl": flag}]}


def test_the_security_events_are_asked_for_the_users_party():
    _resolve_with({})
    urls = [u for u, _ in _RoutedClient.calls if "getAccessPermissions" in u]
    assert [u.split("/api/")[1] for u in urls] == [
        "getAccessPermissions/2000001/INR_QUOTE_DASH_ADMIN",
        "getAccessPermissions/2000001/INR_QUOTE_ADMIN"]


def test_a_dashboard_admin_is_admin_on_every_screen():
    identity = _resolve_with({"INR_QUOTE_DASH_ADMIN": _granted()})
    assert identity.as_body_params()["strUserAdminAccessFl"] == "Y"
    assert identity.as_body_params(crm=True)["strUserAdminAccessFl"] == "Y"


def test_a_quote_admin_is_admin_on_the_crm_screens_only():
    """As the portal: Opportunities checks both events, the quote dashboard one."""
    identity = _resolve_with({"INR_QUOTE_ADMIN": _granted()})
    assert identity.as_body_params()["strUserAdminAccessFl"] == "N"
    assert identity.as_body_params(crm=True)["strUserAdminAccessFl"] == "Y"


def test_no_permission_or_a_failed_check_stays_no():
    for events in ({}, {"INR_QUOTE_DASH_ADMIN": _granted("N")},
                   {"INR_QUOTE_DASH_ADMIN": RuntimeError(), "INR_QUOTE_ADMIN": RuntimeError()}):
        identity = _resolve_with(events)
        assert identity.user_id == "1000001", "the identity itself still resolves"
        assert identity.user_admin_access_fl == identity.crm_admin_access_fl == "N"


def test_a_token_that_already_carries_the_identity_still_gets_the_admin_flags():
    """Claims can carry party and access level; they never carry the admin flags."""
    import httpx
    from globus_mcp_server.auth.user_info import with_admin_flags

    clear_cache()
    original = httpx.AsyncClient
    _RoutedClient.calls, _RoutedClient.events = [], {"INR_QUOTE_DASH_ADMIN": _granted()}
    httpx.AsyncClient = lambda **kw: _RoutedClient(LIVE_RESPONSE)
    try:
        claimed = Identity(user_id="U", party_id="2000001", access_level="1", dept_name="S")
        identity = asyncio.run(with_admin_flags("tok", claimed))
        asyncio.run(with_admin_flags("tok", claimed))
    finally:
        httpx.AsyncClient = original
        clear_cache()
    assert identity.user_admin_access_fl == identity.crm_admin_access_fl == "Y"
    assert identity.party_id == "2000001" and identity.dept_name == "S"
    assert not any("/loadUserInfo/" in u for u, _ in _RoutedClient.calls)
    assert len(_RoutedClient.calls) == 2, "cached for the second call"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all user_info tests passed")
