"""Compare what our tools send with what the portal's own screens send.

Input: a scan file from the browser snippet (globus_endpoints_<page>.json), which
lists every request call in the portal's scripts with the body each one builds.
Each of our tools is called against a recording client, so the comparison is
against exactly what it would send — not a reading of its source.

Usage:
    PYTHONPATH=. python check_portal_drift.py ~/Downloads/globus_endpoints_dashboard.json

Prints, per endpoint we call: OK, or the fields the portal sends that we do not
(missing) and the ones we send that it does not (extra). Then the read endpoints
the portal calls that no tool of ours covers. Exit status 1 when anything differs,
so it can gate a deploy.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import sys
from pathlib import Path
from typing import Any

from globus_mcp_server.auth.base import AuthContext, Identity
from globus_mcp_server.tools import (
    allocation, analysis, context, crm, fulfillment, governance, metrics, opportunities,
    quotes, reference, templates,
)

MODULES = (quotes, fulfillment, allocation, templates, reference, governance, context,
           metrics, opportunities, analysis, crm)

# The client adds these to every POST body (Identity.as_body_params), so they are
# part of what we send even though no tool names them.
IDENTITY_FIELDS = set(Identity().as_body_params())

# Endpoints that change data. The server is read-only; these are listed for
# completeness and never reported as missing tools.
WRITE_PREFIXES = ("save", "update", "add", "remove", "allocate", "confirm", "set",
                  "void", "revert", "send", "calculate", "copy", "revise", "place",
                  "release", "inr", "crm/opp/support/link")


class _Recorder:
    """A client that records calls and answers each with nothing."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def post(self, path, body, auth=None):
        keys = dict(body) if isinstance(body, dict) else {}
        self.calls.append(("POST", path, {**{k: "" for k in IDENTITY_FIELDS}, **keys}))
        return []

    async def get(self, path, auth=None):
        self.calls.append(("GET", path, {}))
        return []

    async def post_envelope(self, path, body, auth=None):
        await self.post(path, body, auth)
        return {"strStatus": "success", "lstDefault": [], "totalCount": 0}


class _Codes:
    async def ensure_defaults(self, auth=None):
        return None

    def __getattr__(self, name):          # any other resolver call: no codes known
        async def _none(*a, **k):
            return {}
        return _none


class _Runtime:
    def __init__(self, client) -> None:
        self.client = client
        self.codes = _Codes()

    async def context(self, call_ctx=None):
        return AuthContext(headers={}, cookies={}, identity=Identity(user_id="u", party_id="p"))


def _placeholder(param: inspect.Parameter) -> Any:
    if param.default is not inspect.Parameter.empty:
        return param.default
    return 1 if param.annotation in (int, "int") else "X-1"


def endpoint_name(path: str) -> str:
    """'loadCompany/p/105401' → 'loadCompany'; 'crm/opp/support/loadProducts/1' kept to its name."""
    path = path.split("?", 1)[0].strip("/")
    parts = [p for p in path.split("/") if p and not p.startswith("{")]
    named = [p for p in parts if re.match(r"[A-Za-z]", p) and not re.fullmatch(r"X-1|\d+|p", p)]
    if named and named[0] == "quotes":
        return "/".join(named[:2])
    if named and named[0] == "crm":
        for i, part in enumerate(named):
            if re.match(r"(load|get|fetch|view|search|notifPref)", part) or i == len(named) - 1:
                return "/".join(named[:i + 2] if part == "search" else named[:i + 1])
    return named[0] if named else path


def our_requests() -> dict[str, dict]:
    """Every tool, called once with placeholders: endpoint → {tool, method, fields}."""
    found: dict[str, dict] = {}
    for module in MODULES:
        for name, fn in inspect.getmembers(module, inspect.iscoroutinefunction):
            if name.startswith("_") or fn.__module__ != module.__name__:
                continue
            params = [p for n, p in inspect.signature(fn).parameters.items()
                      if n not in ("rt", "call_ctx")]
            recorder = _Recorder()
            try:
                asyncio.run(fn(_Runtime(recorder), *[_placeholder(p) for p in params]))
            except Exception:  # noqa: BLE001 — a tool that needs real data still recorded its call
                pass
            for method, path, body in recorder.calls:
                ep = endpoint_name(path)
                entry = found.setdefault(ep, {"tools": [], "method": method, "fields": set()})
                if name not in entry["tools"]:
                    entry["tools"].append(name)
                entry["fields"] |= set(body)
    return found


def _is_write(endpoint: str) -> bool:
    return endpoint.lower().startswith(tuple(p.lower() for p in WRITE_PREFIXES))


def portal_fields(entry: dict) -> set[str] | None:
    """The union of body fields across the portal's calls; None if none parsed."""
    fields: set[str] = set()
    parsed = False
    for body in entry.get("bodies", []):
        if not body or "_unparsed" in body or "params" in body:
            continue
        parsed = True
        fields |= set(body)
    return fields if parsed else None


def main(scan_path: str) -> int:
    scan = json.loads(Path(scan_path).expanduser().read_text())
    ours = our_requests()
    drift = False

    print(f"{'':6}{'endpoint':34}{'tool(s)':42}difference")
    for ep in sorted(ours):
        mine = ours[ep]
        tools = ", ".join(mine["tools"])[:40]
        theirs = scan.get(ep)
        if theirs is None and ep.startswith("crm/"):
            # Opportunities / Tasks are a separate app whose scripts the browser
            # scan does not see. Unverified by this scan — not unused.
            print(f"{'--':6}{ep:34}{tools:42}(Opportunities/Tasks app — not in this scan)")
            continue
        if theirs is None:
            print(f"{'??':6}{ep:34}{tools:42}no screen calls it (the endpoint may still exist)")
            drift = True
            continue
        if theirs["method"] != mine["method"]:
            print(f"{'DIFF':6}{ep:34}{tools:42}method: portal {theirs['method']}, ours {mine['method']}")
            drift = True
            continue
        want = portal_fields(theirs)
        if want is None:
            print(f"{'--':6}{ep:34}{tools:42}(portal body not readable from the scan)")
            continue
        missing = sorted(want - mine["fields"])
        extra = sorted(mine["fields"] - want - IDENTITY_FIELDS)
        if missing or extra:
            drift = True
            print(f"{'DIFF':6}{ep:34}{tools:42}missing={missing} extra={extra}")
        else:
            print(f"{'OK':6}{ep:34}{tools}")

    uncovered = sorted(ep for ep in scan if ep not in ours and not _is_write(ep))
    print(f"\nRead endpoints the portal calls that no tool covers ({len(uncovered)}):")
    for ep in uncovered:
        print(f"  {ep:34}{', '.join(scan[ep]['files'])[:70]}")
    return 1 if drift else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
