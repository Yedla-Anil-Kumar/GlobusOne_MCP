"""Resolve the acting user's Identity from Globus One, given their token.

Okta's token proves *who* the user is and nothing about what they may see. The
five values that decide that — party, access level, company, department, admin
flag — live in `T102_USER_LOGIN`, not in any claim, and Globus One expects them
echoed in the body of every call. So a verified token on its own buys us nothing:
without these, `as_body_params()` sends empty strings and the API returns nothing.

`GET /quote/api/loadUserInfo/<login>` supplies them, authenticated by the same
Bearer token. One call per user per session, cached — this sits in front of every
tool call, so an uncached lookup would double the round trips for the whole agent.

Deliberately resolved SERVER-SIDE from the verified `sub`, never from anything the
caller sends. The portal seeds these values into browser localStorage and posts
them back, which means a user can edit their own access level; replicating that
would make the token decorative.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any, Mapping, Optional

from loguru import logger

from ..config import settings
from .base import Identity

# Response field → Identity attribute. Names match one-for-one, which is why this
# is a mapping rather than a parser: `loadUserInfo` returns exactly the `str*` keys
# `Identity.as_body_params()` sends back.
_FIELD_MAP = {
    "strUserId": "user_id",
    "strPartyId": "party_id",
    "strAccessLevel": "access_level",
    "strCompanyId": "company_id",
    "strDeptName": "dept_name",
    "strUserAdminAccessFl": "user_admin_access_fl",
}

# Identity is stable for the life of a login, so this only needs to outlive a
# conversation, not a session. Short enough that a permissions change takes effect
# the same day without anyone restarting anything.
_CACHE_TTL_SECONDS = 15 * 60

_cache: dict[str, tuple[float, Identity]] = {}

# The security events the portal checks with getAccessPermissions/{partyId}/{event}
# before it loads a dashboard. Either "Y" lifts the territory filter on the CRM
# screens; the quote dashboard looks at DASH_ADMIN alone.
DASH_ADMIN_EVENT = "INR_QUOTE_DASH_ADMIN"
QUOTE_ADMIN_EVENT = "INR_QUOTE_ADMIN"


def username_from_subject(sub: str) -> str:
    """The login name `loadUserInfo` expects, from the token's `sub`.

    Okta's `sub` is the full login (`jdoe@example.com`); the endpoint takes
    the bare name (`/loadUserInfo/jdoe`). Confirmed against a live request — the
    response echoes `strLoginName: "jdoe"`, so a wrong mapping shows up as a 404
    rather than as somebody else's identity.
    """
    return (sub or "").split("@")[0].strip()


def identity_from_user_info(record: Mapping[str, Any]) -> Identity:
    """Build an Identity from one `lstUserInfo` entry.

    Every value is coerced to `str`: `strCompanyId` comes back as an **integer**
    while its siblings are strings, and `Identity` is typed `str` throughout, so an
    uncoerced int would be sent as-is and silently mismatch.

    `strUserAdminAccessFl` is not returned at all. It falls back to Identity's
    default of "N" — the safe direction, since "Y" would assert admin scope nobody
    told us this user has. `resolve_identity` sets it from the security events
    the portal checks.
    """
    values = {
        attr: str(record[field])
        for field, attr in _FIELD_MAP.items()
        if record.get(field) is not None
    }
    return Identity(**values)


def _unwrap_user_info(payload: Any) -> Optional[Mapping[str, Any]]:
    """The single record out of the standard `{strStatus, strMessage, lst*}` envelope."""
    if not isinstance(payload, Mapping):
        return None
    records = payload.get("lstUserInfo")
    if isinstance(records, list) and records and isinstance(records[0], Mapping):
        return records[0]
    return None


def update_access(payload: Any) -> str:
    """`updateAccessFl` of the first `lstAccessPermission` entry, as the portal reads it."""
    records = payload.get("lstAccessPermission") if isinstance(payload, Mapping) else None
    if isinstance(records, list) and records and isinstance(records[0], Mapping):
        return "Y" if str(records[0].get("updateAccessFl") or "").upper() == "Y" else "N"
    return "N"


async def _admin_flags(http: Any, token: str, party_id: str) -> tuple[str, str]:
    """(quote-screen flag, CRM flag). Any failure reads as "N", as before."""
    if not party_id:
        return "N", "N"
    flags = {}
    for event in (DASH_ADMIN_EVENT, QUOTE_ADMIN_EVENT):
        url = f"{settings.api_base.rstrip('/')}/getAccessPermissions/{party_id}/{event}"
        try:
            resp = await http.get(url, headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            flags[event] = update_access(resp.json())
        except Exception as exc:  # noqa: BLE001 — "N" is the flag the user had before
            logger.warning(f"[auth] getAccessPermissions {event} failed for party {party_id}: {exc}")
            flags[event] = "N"
    dash = flags[DASH_ADMIN_EVENT]
    crm = "Y" if "Y" in flags.values() else "N"
    return dash, crm


_flag_cache: dict[str, tuple[float, tuple[str, str]]] = {}


async def with_admin_flags(token: str, identity: Identity) -> Identity:
    """`identity` with the admin flags read from Globus One — for a token whose
    claims already carry the rest, so loadUserInfo is never called. Never raises."""
    party = identity.party_id
    if not party or not token:
        return identity
    cached = _flag_cache.get(party)
    if cached and cached[0] > time.monotonic():
        flags = cached[1]
    else:
        try:
            import httpx

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout)
            ) as http:
                flags = await _admin_flags(http, token, party)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[auth] admin flags unavailable for party {party}: {exc}")
            return identity
        _flag_cache[party] = (time.monotonic() + _CACHE_TTL_SECONDS, flags)
        logger.info(f"[auth] party {party} (from token claims): "
                    f"dash_admin={flags[0]} crm_admin={flags[1]}")
    return dataclasses.replace(identity, user_admin_access_fl=flags[0],
                               crm_admin_access_fl=flags[1])


async def resolve_identity(token: str, sub: str) -> Identity:
    """Fetch and cache the acting user's Identity. Never raises.

    Returns an empty Identity on any failure, which is how the rest of this module
    fails closed: no identity means Globus One returns no data, rather than data
    belonging to whoever the service account happens to be.
    """
    username = username_from_subject(sub)
    if not username or not token:
        return Identity()

    cached = _cache.get(username)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    url = f"{settings.api_base.rstrip('/')}/loadUserInfo/{username}"
    try:
        import httpx  # local import; mirrors GlobusClient's live-only dependency

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.read_timeout, connect=settings.connect_timeout)
        ) as http:
            resp = await http.get(url, headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            record = _unwrap_user_info(resp.json())
            flags = await _admin_flags(
                http, token, str((record or {}).get("strPartyId") or ""))
    except Exception as exc:  # noqa: BLE001 — a lookup failure must not raise into a tool
        logger.error(f"[auth] loadUserInfo failed for {username!r}: {exc}")
        return Identity()

    if record is None:
        logger.error(f"[auth] loadUserInfo returned no record for {username!r}")
        return Identity()

    identity = dataclasses.replace(identity_from_user_info(record),
                                   user_admin_access_fl=flags[0], crm_admin_access_fl=flags[1])
    if not identity.user_id:
        logger.error(f"[auth] loadUserInfo gave no strUserId for {username!r}")
        return Identity()

    _cache[username] = (time.monotonic() + _CACHE_TTL_SECONDS, identity)
    logger.info(
        f"[auth] resolved {username!r}: user_id={identity.user_id} "
        f"party={identity.party_id} access_level={identity.access_level} "
        f"dept={identity.dept_name} company={identity.company_id} "
        f"dash_admin={identity.user_admin_access_fl} crm_admin={identity.crm_admin_access_fl}"
    )
    return identity


def clear_cache() -> None:
    """For tests, and for forcing a re-read after a permissions change."""
    _cache.clear()
    _flag_cache.clear()
