"""End-to-end check of the per-user auth flow (NEU-976), against one real token.

Session-cookie auth stopped working on erptest3 in September 2026 — every
`/quote/api/` call returns 401, including from a logged-in browser. Token
passthrough is now the only way the widget can reach Globus One, so this script
answers the questions that were blocking the build, from a single token:

  1. What claims does Okta actually put in it, and what shape is `sub`?
  2. Does it verify against the configured Okta issuer via JWKS?
  3. Does `loadUserInfo` accept it as a Bearer, called server-to-server?
  4. Which of the six identity fields does that response carry?

The token itself is never printed. Identifiers we already hold in config (`sub`,
`iss`, `aud`, the six identity fields) are shown in full because seeing them is
the point; session and client identifiers (`jti`, `cid`, `uid`) are redacted. The
output is meant to be pasteable into a ticket without a second thought.

Usage (the token never reaches your shell history):

    read -rs -p 'Bearer token: ' GLOBUS_END_USER_TOKEN; echo
    export GLOBUS_END_USER_TOKEN
    GLOBUS_OKTA_ISSUER=https://globusmedical.oktapreview.com/oauth2/default \
    GLOBUS_TOKEN_AUDIENCE=api://default \
      python verify_token.py
"""

from __future__ import annotations

import os
import sys

import httpx
import jwt

from globus_mcp_server.config import settings

# The six values Globus One expects echoed in every POST body — Identity.as_body_params().
REQUIRED_IDENTITY = [
    ("strUserId", "sub / end_user_ref"),
    ("strPartyId", "party_id"),
    ("strAccessLevel", "access_level"),
    ("strCompanyId", "company_id"),
    ("strDeptName", "dept_name"),
    ("strUserAdminAccessFl", "user_admin_access_fl"),
]


# Claims and fields that are identifiers rather than credentials. We already hold
# equivalents in config (GLOBUS_USER_ID, GLOBUS_PARTY_ID), and seeing them is the
# whole point of running this — masking them would make the output useless.
_SHOW_PLAINLY = {
    "iss", "aud", "sub", "end_user_ref", "ver", "iat", "exp", "auth_time", "groups",
    "party_id", "access_level", "company_id", "dept_name", "user_admin_access_fl",
    "strUserId", "strPartyId", "strAccessLevel", "strCompanyId", "strDeptName",
    "strUserAdminAccessFl",
}


def _mask(value, key: str = "") -> str:
    """Show identifiers; redact anything that could be a credential.

    `jti`, `cid` and `uid` are masked not because they are secret but because they
    identify the session and the OAuth client, and this output is meant to be
    pasteable into a ticket without a second thought.
    """
    text = str(value)
    if key in _SHOW_PLAINLY:
        return text
    if len(text) <= 3:
        return text
    return f"{text[:4]}…{text[-2:]} ({len(text)} chars)"


def step(n: int, title: str) -> None:
    print(f"\n{'─' * 68}\n{n}. {title}\n{'─' * 68}")


def main() -> int:
    token = (os.environ.get("GLOBUS_END_USER_TOKEN") or "").strip()
    # Copying the whole header value is the obvious slip; accept it rather than
    # failing with an opaque "not a readable JWT".
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        print("Set GLOBUS_END_USER_TOKEN first (see the module docstring).")
        return 2
    if token.count(".") != 2:
        print(f"That does not look like a JWT (expected 2 dots, found {token.count('.')}).")
        print("Paste only the value after 'Bearer ' from the Authorization header.")
        return 2

    # ── 1. what is actually in it ──────────────────────────────────────────
    step(1, "Claims (decoded WITHOUT verification — just to see the shape)")
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except Exception as exc:  # noqa: BLE001
        print(f"   Not a readable JWT: {exc}")
        return 1

    for key in sorted(claims):
        print(f"   {key:<22} {_mask(claims[key], key)}")

    sub = str(claims.get("sub") or "")
    username = sub.split("@")[0] if "@" in sub else sub
    print(f"\n   sub                    {sub}")
    print(f"   → username for the URL {username}"
          f"{'  (stripped at @)' if '@' in sub else '  (used as-is)'}")

    # ── 2. does it verify the way we verify it ─────────────────────────────
    step(2, "Verification via Okta JWKS (what token.py / PassthroughAuth do)")
    issuer = settings.okta_issuer or os.environ.get("GLOBUS_OKTA_ISSUER", "")
    audience = settings.okta_audience
    print(f"   issuer   {issuer or '(not configured)'}")
    print(f"   audience {audience}")
    if not issuer:
        print("   SKIPPED — set GLOBUS_OKTA_ISSUER to check this.")
    else:
        try:
            from jwt import PyJWKClient
            key = PyJWKClient(f"{issuer.rstrip('/')}/v1/keys").get_signing_key_from_jwt(token).key
            jwt.decode(token, key, algorithms=["RS256"], audience=audience,
                       issuer=issuer.rstrip("/"), options={"require": ["exp"]})
            print("   VERIFIED — signature, expiry, audience and issuer all good.")
        except Exception as exc:  # noqa: BLE001
            print(f"   FAILED — {type(exc).__name__}: {exc}")
            print("   (aud/iss mismatch here means our config needs their exact values)")

    # ── 3. does loadUserInfo take it, server-to-server ─────────────────────
    step(3, "loadUserInfo with the Bearer token (no browser, no cookie)")
    url = f"{settings.api_base.rstrip('/')}/loadUserInfo/{username}"
    print(f"   GET {url}")
    try:
        resp = httpx.get(url, headers={"Authorization": f"Bearer {token}"},
                         timeout=30.0, follow_redirects=False)
    except Exception as exc:  # noqa: BLE001
        print(f"   REQUEST FAILED — {type(exc).__name__}: {exc}")
        return 1

    print(f"   HTTP {resp.status_code}  ({resp.headers.get('content-type', '?')})")
    if resp.status_code != 200:
        print(f"   body: {resp.text[:200]}")
        print("\n   401 here means the token is not accepted for this endpoint;")
        print("   404 means the username in the path is wrong (check the sub mapping).")
        return 1

    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        print(f"   Not JSON: {resp.text[:200]}")
        return 1

    # ── 4. does it carry what we need ──────────────────────────────────────
    step(4, "Identity fields (names and presence — values masked)")
    flat = payload if isinstance(payload, dict) else {}
    for container in ("data", "result", "userInfo", "lstUserInfo"):
        inner = flat.get(container)
        if isinstance(inner, dict):
            flat = {**flat, **inner}
        elif isinstance(inner, list) and inner and isinstance(inner[0], dict):
            flat = {**flat, **inner[0]}

    print("   all top-level keys returned:")
    print(f"     {sorted(payload)[:20]}\n" if isinstance(payload, dict) else f"     {type(payload).__name__}\n")

    missing = []
    for field, claim in REQUIRED_IDENTITY:
        if field in flat:
            print(f"   ✓ {field:<24} {_mask(flat[field], field)}")
        else:
            missing.append(field)
            print(f"   ✗ {field:<24} MISSING  (needed for {claim})")

    print()
    if missing:
        print(f"   {len(missing)} of 6 identity fields are absent under those exact names.")
        print("   If they are present under different names, they are in the key list above.")
    else:
        print("   All six present — we can build the resolver directly against this.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
