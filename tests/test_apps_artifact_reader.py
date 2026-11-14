"""The Apps artifact reader resolves exact fixed references across local mounts."""

from hashlib import sha256

import pytest

from inferrsi.apps.artifact_reader import ArtifactReader
from inferrsi.harness.errors import PublicError
from inferrsi.harness.run_store import RunStore, artifact_ref
from tests.h01_run_store_child import make_attempt, make_digest, make_payload


def _config(tmp_path):
    return {
        "schema_version": 1,
        "archive_namespace": "apps",
        "harness_namespace": "demo",
        "mounts": [
            {"kind": "directory", "namespace": "apps", "prefix": "archive/apps",
             "root": str(tmp_path / "archive")},
            {"kind": "harness_sqlite", "namespace": "demo", "prefix": "stores/demo",
             "root": str(tmp_path / "store")},
        ],
    }


def _ref(location, data):
    return {"location": location, "sha256": sha256(data).hexdigest(),
            "size_bytes": len(data), "media_type": "application/octet-stream"}


def test_read_fixed_record_and_resource_from_distinct_mounts(tmp_path):
    config = _config(tmp_path)
    archive = tmp_path / "archive"
    archive.mkdir()
    resource = b"frozen Apps resource"
    (archive / "input.bin").write_bytes(resource)
    store = RunStore(tmp_path / "store", "demo")
    attempt = make_attempt()
    digest = make_digest(attempt)
    evidence = b"retained Harness evidence"
    blob = artifact_ref("demo", evidence, "application/octet-stream")
    with store.claim(attempt, digest) as claim:
        first = claim.commit(
            make_payload(attempt, digest, 1, "running", artifact_ref=blob),
            [(blob, evidence)],
        )
        claim.commit(make_payload(attempt, digest, 2, "finished"))

    reader = ArtifactReader(config)
    with reader.open(_ref("archive/apps/input.bin", resource)) as stream:
        assert stream.read() == resource
    with reader.open(blob) as stream:
        assert stream.read() == evidence
    with reader.open(first.record_ref["artifact"]) as stream:
        assert stream.read() == first.payload_bytes


def test_read_fixed_harness_ref_from_explicit_offline_export(tmp_path):
    config = _config(tmp_path)
    config["mounts"][1]["kind"] = "directory"
    exported = tmp_path / "store" / "attempts"
    exported.mkdir(parents=True)
    data = b"exported fixed bytes"
    (exported / "v1.json").write_bytes(data)
    reader = ArtifactReader(config)
    ref = _ref("stores/demo/attempts/v1.json", data)
    with reader.open(ref) as stream:
        assert stream.read() == data
    with pytest.raises(PublicError, match="ArtifactIntegrityError") as caught:
        reader.open(_ref(ref["location"], b"different bytes"))
    assert caught.value.stage == "open"


@pytest.mark.parametrize(
    ("location", "code"),
    [
        ("archive/apps-extra/file.bin", "UnsafeArtifactPath"),
        ("archive/apps", "UnsafeArtifactPath"),
        ("archive/apps/../file.bin", "UnsafeArtifactPath"),
        ("archive/apps/.pending-temp/file.bin", "UnsafeArtifactPath"),
        ("archive/apps/missing.bin", "ArtifactUnavailable"),
        ("archive/apps/file.bin", "ArtifactIntegrityError"),
    ],
)
def test_reader_rejects_bad_location_or_bytes(tmp_path, location, code):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "file.bin").write_bytes(b"changed")
    reader = ArtifactReader(_config(tmp_path))
    with pytest.raises(PublicError, match=code) as caught:
        reader.open(_ref(location, b"frozen"))
    assert caught.value.stage == "open"


@pytest.mark.parametrize("prefix", ["archive/apps/overlap", "different/demo"])
def test_reader_rejects_invalid_mounts_before_open(tmp_path, prefix):
    config = _config(tmp_path)
    config["mounts"][1]["prefix"] = prefix
    with pytest.raises(PublicError, match="InvalidRequest") as caught:
        ArtifactReader(config)
    assert caught.value.stage == "storage_config"
