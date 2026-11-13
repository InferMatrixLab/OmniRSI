"""Apps assembles offline assessment from a fixed record and publishes its result."""

import subprocess
import sys
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from inferrsi._json_v1 import decode, encode
from inferrsi.apps import archive
from inferrsi.apps.api import assess as run_assess
from inferrsi.apps.artifact_reader import ArtifactReader
from inferrsi.harness.errors import PublicError
from inferrsi.harness.run_store import RunStore

_FIXTURES = Path(__file__).resolve().parents[1] / "docs/contracts/fixtures/t108-v4"


def _fixture(name):
    return decode((_FIXTURES / f"{name}.json").read_bytes())


@pytest.fixture
def offline(tmp_path):
    directory = tmp_path / "export"
    directory.mkdir()
    for name in ("record-payload.json", "prepared.json", "protocol.json"):
        (directory / name).write_bytes((_FIXTURES / name).read_bytes())
    config = _fixture("storage-config")
    config["mounts"][0]["root"] = str(directory)
    config["mounts"][1]["root"] = str(tmp_path / "unused-store")
    return _fixture("apps-assess-request"), config, directory


def _validate_assessment(assessment):
    for claim in assessment["claims"]:
        if claim["verdict"] not in {"PASS", "FAIL", "INCONCLUSIVE"}:
            raise ValueError("invalid verdict")


def assess(request, **options):
    options.setdefault("encode_assessment", encode)
    return run_assess(request, validate_assessment=_validate_assessment, **options)


def _never_assess(*args, **kwargs):
    pytest.fail("the assessor must not be called")


def _golden_assessor(prepared, evidence, *, artifacts):
    assert prepared == _fixture("prepared")
    assert evidence == _fixture("evidence-input")
    with artifacts.open(prepared["protocol"]) as stream:
        assert stream.read() == (_FIXTURES / "protocol.json").read_bytes()
    return _fixture("assessment")


def _sign_assessment(assessment):
    projection = deepcopy(assessment)
    del projection["assessment_id"], projection["assessed_at"]
    for field in ("prepared_ref", "record_ref"):
        del projection[field]["artifact"]["location"]
    assessment["assessment_id"] = "sha256:" + sha256(encode(projection)).hexdigest()


def _write_export(directory, name, data):
    (directory / name).write_bytes(data)
    return {
        "location": f"fixtures/{name}", "media_type": "application/json",
        "sha256": sha256(data).hexdigest(), "size_bytes": len(data),
    }


@pytest.mark.parametrize(("primary", "exit_code"), [("F", 0), ("C", 8), ("M", 9)])
def test_primary_claim_controls_exit_without_dropping_other_claims(tmp_path, offline, primary,
                                                               exit_code):
    request, config, _ = offline
    request["primary_claim_id"] = primary
    decision = _fixture("assessment")
    decision["claims"][0]["verdict"] = "PASS"
    decision["claims"][1]["verdict"] = "FAIL"
    _sign_assessment(decision)

    def assessor(prepared, evidence, *, artifacts):
        _golden_assessor(prepared, evidence, artifacts=artifacts)
        return decision

    output = assess(request, workspace=tmp_path, storage_config=config,
                    assess_benchmark=assessor, output_id="offline")

    assert output.result["exit_code"] == exit_code
    assert output.result["assessment"] == decision
    assert output.result["record_ref"] == request["record_ref"]
    assert output.result["execution"] is None
    assert archive.read_result(tmp_path, "offline").result == output.result
    ref = output.result["assessment_ref"]
    assert archive.read_assessment(config, ref) == decision
    with ArtifactReader(config).open(ref["artifact"]) as stream:
        assert stream.read() == encode(decision)


@pytest.mark.parametrize(("field", "value"), [("primary_claim_id", "other"),
                                             ("primary_scope", "other")])
def test_unknown_primary_claim_does_not_invoke_assessor(tmp_path, offline, field, value):
    request, config, directory = offline
    request[field] = value
    output = assess(request, workspace=tmp_path, storage_config=config,
                    assess_benchmark=_never_assess)
    assert output.result["exit_code"] == 2
    assert output.result["error"]["code"] == "InvalidRequest"
    assert output.result["error"]["stage"] == "primary_claim"
    assert not list(directory.glob("*/assessment.json"))


@pytest.mark.parametrize("fault", [
    "omitted_claim", "duplicate_claim", "identity", "foreign_error", "invalid_verdict", "encoding",
])
def test_invalid_assessor_response_does_not_create_archive(tmp_path, offline, fault):
    request, config, directory = offline
    decision = _fixture("assessment")
    if fault == "omitted_claim":
        del decision["claims"][1]
        _sign_assessment(decision)
    elif fault == "duplicate_claim":
        decision["claims"].append(decision["claims"][0])
        _sign_assessment(decision)
    elif fault == "identity":
        decision["assessment_id"] = "sha256:" + "0" * 64
    elif fault == "invalid_verdict":
        decision["claims"][0]["verdict"] = "other"
        _sign_assessment(decision)

    def assessor(*args, **kwargs):
        if fault == "foreign_error":
            raise PublicError("AssessorError", stage="assess", message="test", attempt_id="foreign")
        return decision

    encoder = (lambda assessment: b"{}") if fault == "encoding" else encode
    output = assess(request, workspace=tmp_path, storage_config=config,
                    assess_benchmark=assessor, encode_assessment=encoder)
    assert output.result["exit_code"] == 2
    assert output.result["error"]["code"] == "AssessorError"
    assert output.result["error"]["stage"] == "assessor_response_validation"
    assert output.result["assessment_ref"] is None
    assert not list(directory.glob("*/assessment.json"))


def test_assessor_cannot_read_unrelated_or_foreign_attempt_artifacts(tmp_path, offline):
    request, config, directory = offline
    record = _fixture("record-payload")
    present = _write_export(directory, "retained.json", b"{}")
    foreign = _write_export(directory, "foreign.json", b"{}")
    unrelated = _write_export(directory, "unrelated.json", b"{}")
    missing = {**present, "location": "fixtures/missing.json"}
    record["checks"][0]["evidence_refs"] = [present, missing]
    record["checks"].append({
        **record["checks"][0], "attempt_id": "other", "evidence_refs": [foreign],
    })
    request["record_ref"]["artifact"] = _write_export(
        directory, "record-payload.json", encode(record),
    )

    def assessor(prepared, evidence, *, artifacts):
        assert evidence["record"] == record
        with artifacts.open(present) as stream:
            assert stream.read() == b"{}"
        for ref in (missing, foreign, unrelated):
            with pytest.raises(PublicError, match="ArtifactUnavailable"):
                artifacts.open(ref)
        decision = _fixture("assessment")
        decision["record_ref"] = evidence["record_ref"]
        _sign_assessment(decision)
        return decision

    output = assess(request, workspace=tmp_path, storage_config=config, assess_benchmark=assessor)
    assert output.result["exit_code"] == 9
    assert output.result["assessment_ref"] is not None


def test_wrong_record_version_is_rejected_before_assessing(tmp_path, offline):
    request, config, _ = offline
    request["record_ref"]["record_version"] += 1
    output = assess(request, workspace=tmp_path, storage_config=config,
                    assess_benchmark=_never_assess)
    assert output.result["error"]["code"] == "IdentityMismatch"
    assert output.result["assessment"] is None


def test_assessor_programming_error_reaches_the_host(tmp_path, offline):
    request, config, directory = offline

    def broken_assessor(*args, **kwargs):
        raise TypeError("fixture programming error")

    with pytest.raises(TypeError, match="fixture programming error"):
        assess(request, workspace=tmp_path, storage_config=config, assess_benchmark=broken_assessor)
    assert not list(directory.glob("*/assessment.json"))


def test_read_assessment_rejects_the_wrong_identity_even_if_bytes_match(offline):
    _, config, directory = offline
    data = (_FIXTURES / "assessment.json").read_bytes()
    ref = _fixture("fixed-refs")["assessment_ref"]
    ref["artifact"] = _write_export(directory, "assessment.json", data)
    ref["assessment_id"] = "sha256:" + "0" * 64
    with pytest.raises(PublicError, match="IdentityMismatch"):
        archive.read_assessment(config, ref)


def test_new_process_reassesses_the_requested_sqlite_revision(tmp_path, offline):
    request, config, _ = offline
    store = RunStore(config["mounts"][1]["root"], config["harness_namespace"])
    spec = _fixture("execution-spec")
    attempt = {field: spec[field] for field in (
        "request_id", "run_id", "attempt_id", "owner", "target_request", "deadline_utc",
        "resources", "budget", "retry_of", "retry_reason",
    )}
    payload = _fixture("record-payload")
    with store.claim(attempt, payload["request_digest"]) as claim:
        first = claim.commit({**payload, "phase": "running", "started": True,
                              "process_outcome": None})
        latest = claim.commit({**payload, "record_version": 2})
    request["record_ref"] = first.record_ref
    decision = _fixture("assessment")
    decision["record_ref"] = first.record_ref
    _sign_assessment(decision)
    (tmp_path / "offline.json").write_bytes(encode([request, config, decision]))

    subprocess.run(
        [sys.executable, "-c", """
from pathlib import Path
from inferrsi._json_v1 import decode, encode
from inferrsi.apps.api import assess as run_assess

workspace = Path.cwd()
request, config, decision = decode((workspace / 'offline.json').read_bytes())

def assessor(prepared, evidence, *, artifacts):
    assert evidence['record_ref'] == request['record_ref']
    assert evidence['record']['phase'] == 'running'
    return decision

output = run_assess(request, workspace=workspace, storage_config=config,
                    assess_benchmark=assessor, validate_assessment=lambda value: None,
                    encode_assessment=encode,
                    output_id='reassessed')
assert output.result['exit_code'] == 9
"""],
        cwd=tmp_path, capture_output=True, text=True, check=True,
    )
    result = archive.read_result(tmp_path, "reassessed").result
    assert result["record_ref"] == first.record_ref
    assert store.read(attempt["attempt_id"])["record_ref"] == latest.record_ref


@pytest.mark.parametrize("stage", ["assessment_archive", "output_publication"])
def test_publication_failure_preserves_only_committed_references(tmp_path, offline, monkeypatch,
                                                                stage):
    request, config, directory = offline
    destination_root = directory if stage == "assessment_archive" else tmp_path / "output"
    original_publish = archive._publish_directory

    def fail_at_stage(staging, destination):
        if destination.parent == destination_root:
            raise OSError("test failure")
        original_publish(staging, destination)

    monkeypatch.setattr(archive, "_publish_directory", fail_at_stage)
    output = assess(request, workspace=tmp_path, storage_config=config,
                    assess_benchmark=_golden_assessor, output_id="offline")
    result = output.result
    assert result["exit_code"] == 6
    assert result["error"]["code"] == "ArchivePersistenceError"
    assert result["error"]["stage"] == stage
    assert result["assessment"] == _fixture("assessment")
    assert result["record_ref"] == request["record_ref"]
    if stage == "assessment_archive":
        assert result["assessment_ref"] is None
        assert archive.read_result(tmp_path, "offline").result == result
    else:
        assert output.directory is None
        with ArtifactReader(config).open(result["assessment_ref"]["artifact"]) as stream:
            assert stream.read() == (_FIXTURES / "assessment.json").read_bytes()
