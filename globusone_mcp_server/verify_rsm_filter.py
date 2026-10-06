"""Why does filtering by an RSM the portal shows return nothing?

The portal lists five opportunities under "Alessio Lentati (Italy)";
`list_opportunities(rsm="Alessio Lentati (Italy)")` returns zero, with no error —
indistinguishable from "this RSM has no deals", which is what the user was told.

`strRsm` is passed to the dashboard endpoint verbatim (see _FILTER_MAP), so the
question is only which spelling that endpoint matches. This prints the values the
API itself advertises and tries each candidate against them.

Usage (on the VM, MCP server running; same token as verify_live.py):
    GLOBUS_END_USER_TOKEN=... PYTHONPATH=~/globus_mcp_server \\
      python ~/globus_mcp_server/verify_rsm_filter.py "Alessio Lentati"
"""

import os
import sys
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from globus_mcp_server.config import settings  # noqa: E402

URL = f"http://127.0.0.1:{settings.port}/mcp"


async def _json(session, name, args):
    result = await session.call_tool(name, args)
    text = result.content[0].text if result.content else ""
    if getattr(result, "isError", False):
        return {"_error": text[:200]}
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return {"_text": text[:200]}


async def main():
    who = sys.argv[1] if len(sys.argv) > 1 else "Alessio Lentati"

    token = (os.environ.get("GLOBUS_END_USER_TOKEN") or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    headers = {"Authorization": f"Bearer {token}"} if token else None
    print(f"end-user token: {'sent' if token else 'not set (service mode)'}\n")

    async with streamablehttp_client(URL, headers=headers) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()

            options = await _json(session, "get_filter_options", {})
            result = (options or {}).get("objResult") or options or {}
            rsms = result.get("rsms") or []
            print(f"get_filter_options advertises {len(rsms)} RSM value(s):")
            for name in rsms:
                print(f"  {name!r}")

            matches = [n for n in rsms if who.lower() in str(n).lower()]
            print(f"\ncontaining {who!r}: {matches or 'NONE — not a filterable value'}\n")

            # Every spelling the model could plausibly send, against the one it
            # actually sent. If a bare name matches where the displayed string
            # does not, the tool must reconcile the two rather than pass them on.
            candidates = [who, f"{who} (Italy)", *matches]
            seen = set()
            for candidate in candidates:
                if candidate in seen:
                    continue
                seen.add(candidate)
                rows = await _json(
                    session, "list_opportunities", {"rsm": candidate, "page_size": 200}
                )
                total = rows.get("total") if isinstance(rows, dict) else None
                print(f"rsm={candidate!r:40} -> total={total} "
                      f"returned={rows.get('returned') if isinstance(rows, dict) else rows}")

            # The control: EVERY page, not the first. One page of 200 against a
            # total of 483 cannot show that an owner is absent — it only shows
            # they were not in the first 200, which is not the same claim.
            owners: dict[str, int] = {}
            total = 0
            swept = 0
            for page in range(1, 11):
                batch = await _json(
                    session, "list_opportunities", {"page": page, "page_size": 200}
                )
                rows = batch.get("opportunities") or []
                total = batch.get("total") or total
                swept += len(rows)
                for row in rows:
                    name = str(row.get("rsm") or "(blank)")
                    owners[name] = owners.get(name, 0) + 1
                if not rows or swept >= (total or 0):
                    break

            print(f"\nRSM values across ALL {swept} rows (of {total} reported):")
            for name, count in sorted(owners.items(), key=lambda kv: -kv[1]):
                mark = "" if name in rsms else "   <- NOT a filterable value"
                print(f"  {count:4d}  {name!r}{mark}")

            missing = [n for n in rsms if n not in owners]
            print(f"\nAdvertised but owning none of the {swept} rows ({len(missing)}):")
            for name in missing:
                print(f"  {name!r}")

            # Does the filter work for anyone? If a name that DOES own rows also
            # returns 0, the filter itself is broken rather than the data missing.
            print("\nfilter check — a name that owns rows here:")
            for name, count in sorted(owners.items(), key=lambda kv: -kv[1])[:2]:
                if name in rsms:
                    got = await _json(
                        session, "list_opportunities", {"rsm": name, "page_size": 200}
                    )
                    print(f"  rsm={name!r:40} owns {count} -> filter returns "
                          f"total={got.get('total')}")

            # Is it the PERSON or the REGION that is out of scope? The portal
            # showed all five of those deals under zone director "Jegor Tsarou".
            # If no zone director from that region owns a single row either, the
            # dashboard is scoped to a subset of the CRM rather than mis-filtering
            # one name.
            zds: dict[str, int] = {}
            ads: dict[str, int] = {}
            for page in range(1, 11):
                batch = await _json(
                    session, "list_opportunities", {"page": page, "page_size": 200}
                )
                got = batch.get("opportunities") or []
                for row in got:
                    zds[str(row.get("zoneDirector") or "(blank)")] = \
                        zds.get(str(row.get("zoneDirector") or "(blank)"), 0) + 1
                    ads[str(row.get("areaDirector") or "(blank)")] = \
                        ads.get(str(row.get("areaDirector") or "(blank)"), 0) + 1
                if not got:
                    break

            for title, seen, advertised in (
                ("zone director", zds, result.get("zones") or []),
                ("area director", ads, result.get("areaDirectors") or []),
            ):
                print(f"\n{title}s owning rows ({len(seen)}):")
                for name, count in sorted(seen.items(), key=lambda kv: -kv[1]):
                    print(f"  {count:4d}  {name!r}")
                absent = [n for n in advertised if n not in seen]
                print(f"{title}s advertised but owning none ({len(absent)}): {absent}")

            # The sharpest test of all: look the missing deals up by ACCOUNT. A
            # name the portal displays that this endpoint cannot find by account
            # is not a filter problem — the record is not in this result set.
            for account in sys.argv[2:]:
                found = await _json(
                    session, "list_opportunities",
                    {"account_name": account, "page_size": 200},
                )
                print(f"\naccount_name={account!r} -> total={found.get('total')}")
                for row in (found.get("opportunities") or [])[:5]:
                    print(f"    {row.get('name')!r} rsm={row.get('rsm')!r} "
                          f"zd={row.get('zoneDirector')!r}")

            print(
                "\nRead it this way: an advertised name owning no rows means the "
                "dashboard does not return that owner's records to this user at "
                "all — not that the filter string is wrong. A name that owns rows "
                "but still filters to 0 would mean the filter itself is broken."
            )


if __name__ == "__main__":
    asyncio.run(main())
