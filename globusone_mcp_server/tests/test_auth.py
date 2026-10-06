"""Option A passthrough-auth verification tests (NEU-976).

Mints tokens with the shared secret and asserts PassthroughAuth._verify accepts
valid ones and fails closed (empty Identity) on expired / wrong-audience /
bad-signature / missing-exp tokens.

Run: .venv/bin/python globus_mcp_server/tests/test_auth.py
"""

from __future__ import annotations

import datetime as _dt

import jwt

from globus_mcp_server.auth.passthrough import PassthroughAuth

SECRET = "test-shared-secret"
AUD = "corporate-gpt-widget"
ISS = "globusone"


def _mint(**overrides) -> str:
    now = _dt.datetime.now(_dt.timezone.utc)
    claims = {
        "iss": ISS,
        "aud": AUD,
        "sub": "1000001",
        "party_id": "2000002",
        "access_level": "3",
        "iat": now,
        "exp": now + _dt.timedelta(minutes=10),
    }
    claims.update(overrides)
    secret = overrides.pop("_secret", SECRET)
    return jwt.encode(claims, secret, algorithm="HS256")


def _auth() -> PassthroughAuth:
    return PassthroughAuth(secret=SECRET, audience=AUD, issuer=ISS)


def test_valid_token_yields_identity():
    ident = _auth()._verify(_mint())
    assert ident.user_id == "1000001"
    assert ident.party_id == "2000002"
    assert ident.access_level == "3"


def test_expired_token_fails_closed():
    now = _dt.datetime.now(_dt.timezone.utc)
    tok = _mint(exp=now - _dt.timedelta(minutes=1))
    assert _auth()._verify(tok).user_id == ""


def test_wrong_audience_fails_closed():
    tok = _mint(aud="someone-else")
    assert _auth()._verify(tok).user_id == ""


def test_bad_signature_fails_closed():
    tok = jwt.encode(
        {"iss": ISS, "aud": AUD, "sub": "x",
         "exp": _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(minutes=5)},
        "WRONG-secret", algorithm="HS256",
    )
    assert _auth()._verify(tok).user_id == ""


def test_missing_exp_fails_closed():
    tok = jwt.encode({"iss": ISS, "aud": AUD, "sub": "x"}, SECRET, algorithm="HS256")
    assert _auth()._verify(tok).user_id == ""


def test_no_secret_returns_empty():
    assert PassthroughAuth(secret="")._verify(_mint()).user_id == ""


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all auth tests passed")
