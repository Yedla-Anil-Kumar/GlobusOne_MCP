"""Environment-driven configuration.

Key switches:
- ``GLOBUS_DATA_MODE``  — ``fixtures`` (default; offline, HAR-derived payloads)
                          or ``live`` (real HTTP to the Globus One API).
- ``GLOBUS_AUTH_MODE``  — ``service`` (default) or ``passthrough``. The auth
                          decision (NEU-976) is open, so this stays pluggable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_FIXTURES_DIR = (Path(__file__).resolve().parent.parent / "fixtures").resolve()


def _f(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    api_base: str = os.environ.get(
        "GLOBUS_API_BASE", "https://erptest3.globusone.com/quote/api"
    )
    data_mode: str = os.environ.get("GLOBUS_DATA_MODE", "fixtures").lower()
    fixtures_dir: Path = _FIXTURES_DIR

    auth_mode: str = os.environ.get("GLOBUS_AUTH_MODE", "service").lower()
    service_user: str = os.environ.get("GLOBUS_SERVICE_USER", "")
    service_secret: str = os.environ.get("GLOBUS_SERVICE_SECRET", "")
    # Option A (passthrough) token verification.
    # Preferred: Okta-issued JWT verified via JWKS (RS256). Set the Okta issuer,
    # e.g. https://globusmedical.oktapreview.com/oauth2/default — JWKS is then
    # <issuer>/v1/keys. Falls back to an HS256 shared secret for dev.
    okta_issuer: str = os.environ.get("GLOBUS_OKTA_ISSUER", "")
    okta_audience: str = os.environ.get("GLOBUS_TOKEN_AUDIENCE", "corporate-gpt-widget")
    passthrough_secret: str = os.environ.get("GLOBUS_PASSTHROUGH_SECRET", "")

    # DEMO shortcut: a borrowed browser JSESSIONID + the matching identity, so
    # live mode can hit the already-running test portal without the Okta flow.
    # Short-lived and single-user — for demos only, not production.
    session_cookie: str = os.environ.get("GLOBUS_SESSION_COOKIE", "")
    demo_user_id: str = os.environ.get("GLOBUS_USER_ID", "")
    demo_party_id: str = os.environ.get("GLOBUS_PARTY_ID", "")
    demo_access_level: str = os.environ.get("GLOBUS_ACCESS_LEVEL", "")
    demo_dept_name: str = os.environ.get("GLOBUS_DEPT_NAME", "S")
    demo_admin_fl: str = os.environ.get("GLOBUS_ADMIN_FL", "Y")

    connect_timeout: float = _f("GLOBUS_CONNECT_TIMEOUT", 10.0)
    read_timeout: float = _f("GLOBUS_READ_TIMEOUT", 120.0)

    host: str = os.environ.get("GLOBUS_MCP_HOST", "0.0.0.0")
    port: int = int(os.environ.get("GLOBUS_MCP_PORT", "8812"))

    # Paging limits for the two places that sweep the pipeline — the analysis
    # report and the "(not set)" confidence scan. They were declared separately in
    # each module with identical values under different names, so tuning one left
    # the other behind and the two sweeps silently disagreed about how much of the
    # CRM they had seen. Page size is larger than the interactive default (50) to
    # keep a sweep to a couple of requests; the page and row caps are what stop a
    # report becoming an export of the whole CRM.
    sweep_page_size: int = int(os.environ.get("GLOBUS_SWEEP_PAGE_SIZE", "200"))
    sweep_max_pages: int = int(os.environ.get("GLOBUS_SWEEP_MAX_PAGES", "10"))
    sweep_max_rows: int = int(os.environ.get("GLOBUS_SWEEP_MAX_ROWS", "2000"))

    @property
    def is_live(self) -> bool:
        return self.data_mode == "live"


settings = Settings()
