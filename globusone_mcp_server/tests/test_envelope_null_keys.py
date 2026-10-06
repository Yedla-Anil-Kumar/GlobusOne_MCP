"""A data key that is present but null is not the response's data.

Every GlobusOne response is the same envelope: {strStatus, strMessage,
lstDefault, objResult, strResult}. The codebase catalogue lists which key holds
each endpoint's data — set allocation, release-for-sale, in-transit returns and
others use strResult, a JSON string. The captured fixtures omit the unused keys,
but a serializer that writes nulls would have made unwrap return lstDefault=None
for all of those: an empty result from a full response.

Run:  PYTHONPATH=. pytest tests/test_envelope_null_keys.py -q
"""

from globus_mcp_server.client import GlobusClient


def test_strresult_is_read_past_a_null_lstdefault() -> None:
    env = {"strStatus": "success", "lstDefault": None, "objResult": None,
           "strResult": '[{"strSetId": "S-1"}]'}

    assert GlobusClient.unwrap(env) == [{"strSetId": "S-1"}]


def test_objresult_is_read_past_a_null_lstdefault() -> None:
    env = {"strStatus": "success", "lstDefault": None, "objResult": {"overdue": 3}}

    assert GlobusClient.unwrap(env) == {"overdue": 3}


def test_an_empty_list_is_still_the_answer() -> None:
    """Empty is not null: an endpoint that returns lstDefault=[] found nothing."""
    env = {"strStatus": "success", "lstDefault": [], "strResult": None}

    assert GlobusClient.unwrap(env) == []


def test_code_lookups_read_their_own_key() -> None:
    env = {"strStatus": "success", "lstCodeLookUp": [{"codeId": "1"}]}

    assert GlobusClient.unwrap(env) == [{"codeId": "1"}]
