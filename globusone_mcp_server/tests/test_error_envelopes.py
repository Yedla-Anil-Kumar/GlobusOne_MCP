"""An auth failure must never look like an empty pipeline.

GlobusOne signals both "nothing matched" and "your session died" as
``strStatus: "error"`` with a sentence in ``strMessage``, and ``unwrap``
separates them by substring — so the two categories must stay distinguishable.

Run:  .venv/bin/python -m pytest globus_mcp_server/tests -q
"""

from __future__ import annotations

import pytest

from globus_mcp_server.client import GlobusApiError, GlobusClient


def _err(message: str) -> dict:
  return {"strStatus": "error", "strMessage": message}


# Real message shapes seen from the portal, plus the wordings that made the two
# categories collide.
AUTH_FAILURES = [
  "User session not found. Please login again.",
  "No data available for your access level",
  "Session expired",
  "Unauthorized",
  "Permission denied for this function",
  "Login required",
]

EMPTY_RESULTS = [
  "Opportunity list is empty",
  "No record found",
  "No results",
  "User does not exist",
]


@pytest.mark.parametrize("message", AUTH_FAILURES)
def test_auth_failures_raise_rather_than_reading_as_empty(message: str) -> None:
  with pytest.raises(GlobusApiError):
    GlobusClient.unwrap(_err(message))


@pytest.mark.parametrize("message", EMPTY_RESULTS)
def test_genuine_empty_results_still_return_empty(message: str) -> None:
  # These must NOT start raising: "nothing matched" is a normal answer, and
  # turning it into an error would make ordinary questions look broken.
  assert GlobusClient.unwrap(_err(message)) == []


def test_unclassified_errors_still_raise() -> None:
  with pytest.raises(GlobusApiError):
    GlobusClient.unwrap(_err("Internal server error"))


def test_success_envelope_is_untouched() -> None:
  assert GlobusClient.unwrap({"strStatus": "success", "lstDefault": [1, 2, 3]}) == [1, 2, 3]


def test_post_envelope_checks_status_before_returning_the_envelope() -> None:
  """`post_envelope` returns the envelope whole, but must still vet it."""
  import inspect

  source = inspect.getsource(GlobusClient.post_envelope)
  assert "strStatus" in source, "post_envelope must inspect the envelope status"
  assert "self.unwrap" in source, "post_envelope must reuse unwrap's error rules"
