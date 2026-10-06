"""Coded-value resolution.

Globus One stores many fields as numeric code ids (e.g. an opportunity stage is
``262420128``). ``loadCodeLookup`` returns, per code group, the ``codeId → codeNm``
mapping. This cache lazily loads groups and resolves ids to human labels so the
assistant sees "Closed", not a number.
"""

from __future__ import annotations

from typing import Any, Optional

from .auth.base import AuthContext
from .client import GlobusClient

# Code groups worth preloading for opportunity POC (extend as tools grow).
DEFAULT_GROUPS = ("CRMSTG",)


class CodeResolver:
    def __init__(self, client: GlobusClient) -> None:
        self._client = client
        self._by_id: dict[int, str] = {}
        self._loaded_groups: set[str] = set()

    async def load_group(self, code_grp: str, *, auth: AuthContext) -> None:
        if code_grp in self._loaded_groups:
            return
        rows = await self._client.post("loadCodeLookup", {"codeGrp": code_grp}, auth=auth)
        for row in rows or []:
            try:
                self._by_id[int(row["codeId"])] = row.get("codeNm", "")
            except (KeyError, TypeError, ValueError):
                continue
        self._loaded_groups.add(code_grp)

    async def ensure_defaults(self, *, auth: AuthContext) -> None:
        for grp in DEFAULT_GROUPS:
            await self.load_group(grp, auth=auth)

    def resolve(self, code_id: Any) -> Optional[str]:
        """Return the label for a code id, or None if unknown."""
        try:
            return self._by_id.get(int(code_id))
        except (TypeError, ValueError):
            return None

    def relabel(self, record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
        """Return a copy of ``record`` with each coded field resolved to a label.

        For a field ``STAGEID`` we add ``STAGE`` (label) when resolvable, leaving
        the original id in place for traceability.
        """
        out = dict(record)
        for f in fields:
            if f in out:
                label = self.resolve(out[f])
                if label is not None:
                    out[f.removesuffix("ID") or f] = label
        return out
