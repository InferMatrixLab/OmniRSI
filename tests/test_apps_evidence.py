"""Evidence projection retains the exact committed Harness payload."""

from hashlib import sha256
from pathlib import Path

import pytest

from inferrsi._json_v1 import decode, encode
from inferrsi.apps.bridge import to_evidence
from inferrsi.harness.errors import PublicError

_FIXTURES = Path(__file__).resolve().parents[1] / "docs/contracts/fixtures/t108-v4"


@pytest.fixture
def fixed():
    raw = (_FIXTURES / "record-payload.json").read_bytes()
    record = decode(raw)
    prepared = decode((_FIXTURES / "prepared.json").read_bytes())
    expected = decode((_FIXTURES / "evidence-input.json").read_bytes())
    return raw, record, prepared, expected


def _refresh_ref(ref, record):
    data = encode(record)
    ref["artifact"].update(sha256=sha256(data).hexdigest(), size_bytes=len(data))


def test_projection_matches_golden_without_rewriting_record(fixed):
    raw, record, prepared, expected = fixed
    evidence = to_evidence(prepared, record["prepared_ref"], expected["record_ref"], record)
    assert encode(evidence) == (_FIXTURES / "evidence-input.json").read_bytes()
    assert encode(evidence["record"]) == raw
    assert evidence["record_digest"] == sha256(raw).hexdigest()
    evidence["record"]["checks"].clear()
    assert encode(record) == raw


def test_projection_accepts_fixed_nonterminal_snapshot(fixed):
    _, record, prepared, expected = fixed
    record.update(phase="running", process_outcome=None, exit_code=None, started=True)
    record_ref = expected["record_ref"]
    _refresh_ref(record_ref, record)
    evidence = to_evidence(prepared, record["prepared_ref"], record_ref, record)
    assert evidence["record"]["phase"] == "running"
    assert evidence["record"]["process_outcome"] is None


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("observed_at", "2099-01-01T00:00:00Z", "InvalidRequest"),
        ("record_version", 2, "IdentityMismatch"),
        ("attempt_id", "different", "IdentityMismatch"),
        ("validation_scope", "different", "IdentityMismatch"),
        ("protocol_digest", "sha256:" + "0" * 64, "IdentityMismatch"),
        ("prepared_ref", None, "IdentityMismatch"),
        ("candidate", None, "IdentityMismatch"),
    ],
)
def test_projection_rejects_mismatched_or_observed_records(fixed, field, value, code):
    _, record, prepared, expected = fixed
    prepared_ref = record["prepared_ref"]
    record[field] = value
    record_ref = expected["record_ref"]
    _refresh_ref(record_ref, record)
    with pytest.raises(PublicError, match=code):
        to_evidence(prepared, prepared_ref, record_ref, record)


def test_projection_rejects_changed_record_or_prepared_bytes(fixed):
    _, record, prepared, expected = fixed
    prepared_ref = record["prepared_ref"]
    record_ref = expected["record_ref"]
    record["elapsed_seconds"] = "123"
    with pytest.raises(PublicError, match="ArtifactIntegrityError"):
        to_evidence(prepared, prepared_ref, record_ref, record)
    prepared["display_notes"] = "changed bytes"
    with pytest.raises(PublicError, match="IdentityMismatch"):
        to_evidence(prepared, prepared_ref, record_ref, record)


def test_projection_rejects_incomplete_committed_payload(fixed):
    _, record, prepared, expected = fixed
    del record["checks"]
    record_ref = expected["record_ref"]
    _refresh_ref(record_ref, record)
    with pytest.raises(PublicError, match="InvalidRequest"):
        to_evidence(prepared, record["prepared_ref"], record_ref, record)
