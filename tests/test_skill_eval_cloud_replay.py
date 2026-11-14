"""Validate data ingress without executing untrusted code or making network calls."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from tools import skill_eval_cloud_replay as replay


def bundle():
    source = replay.engineering.STARTER
    return {"schema_version": replay.SCHEMA, "task_id": "public-parser-task",
            "request_sha256": replay.digest(replay.engineering.REQUEST.encode()),
            "grader_sha256": replay.digest(Path(replay.engineering.__file__).read_bytes()),
            "inputs_sha256": "a" * 64, "protocol_sha256": "b" * 64,
            "artifacts": [{"arm": arm, "source": source, "sha256": replay.digest(source.encode())}
                          for arm in ("baseline", "candidate")]}


def encoded(value):
    raw = json.dumps(value).encode()
    return raw, replay.digest(raw)


def test_valid_bundle_exact_bytes():
    value = bundle()
    assert replay.validate_bundle(*encoded(value)) == value


@pytest.mark.parametrize("mutation", ["extra", "wrong_request", "wrong_grader", "duplicate_arm",
                                     "source_hash", "big_source", "surrogate", "path", "missing"])
def test_invalid_bundle_rejected_before_qualification(mutation):
    value = bundle()
    if mutation == "extra":
        value["command"] = "untrusted"
    elif mutation in ("wrong_request", "wrong_grader"):
        value[mutation.removeprefix("wrong_") + "_sha256"] = "0" * 64
    elif mutation == "duplicate_arm":
        value["artifacts"][1]["arm"] = "baseline"
    elif mutation == "source_hash":
        value["artifacts"][0]["sha256"] = "0" * 64
    elif mutation == "big_source":
        value["artifacts"][0]["source"] = "x" * 65537
    elif mutation == "surrogate":
        value["artifacts"][0]["source"] = "\ud800"
    elif mutation == "path":
        value["task_id"] = "../../oops"
    else:
        del value["protocol_sha256"]
    with patch.object(replay, "qualify") as qualify, pytest.raises(ValueError):
        replay.replay(*encoded(value), "a" * 40, "test")
    qualify.assert_not_called()


def test_duplicate_json_key_size_and_digest_rejected():
    for raw in (b'{"x":1,"x":2}', b"[" * 2000, b"x" * (replay.MAX_BUNDLE_BYTES + 1), b"\xff"):
        with pytest.raises(ValueError):
            replay.validate_bundle(raw, replay.digest(raw))
    with pytest.raises(ValueError):
        replay.validate_bundle(encoded(bundle())[0], "0" * 64)


def test_qualification_failure_never_executes_artifacts():
    with patch.object(replay, "qualify", return_value={"verdict": "INCONCLUSIVE"}), \
         patch.object(replay.artifact_check, "verify") as verify:
        result = replay.replay(*encoded(bundle()), "a" * 40, "test")
    assert result["verdict"] == "INCONCLUSIVE"
    verify.assert_not_called()


def test_replay_keeps_negative_controls_as_delivery_not_efficacy():
    with patch.object(replay, "qualify", return_value={"verdict": "PASS"}), \
         patch.object(replay.artifact_check, "verify", return_value={"passed": False}) as verify:
        result = replay.replay(*encoded(bundle()), "a" * 40, "test")
    assert result["verdict"] == "PASS" and verify.call_count == 2
    assert all(item["result"]["passed"] is False for item in result["artifacts"])
    assert "source" not in json.dumps(result)


def test_lost_isolation_stays_inconclusive():
    with patch.object(replay, "qualify", return_value={"verdict": "PASS"}), \
         patch.object(replay.artifact_check, "verify",
                      side_effect=replay.artifact_sandbox.SandboxUnavailable):
        assert replay.replay(*encoded(bundle()), "a" * 40, "test")["verdict"] == "INCONCLUSIVE"


def test_no_arbitrary_ingress_url_and_no_redirect():
    for value in ("main", "../evil", "https://evil", "a" * 40 + ";id"):
        with pytest.raises(ValueError):
            replay.fetch_bundle(value)
    with pytest.raises(ValueError):
        replay.NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere")


def test_workflow_inputs_not_interpolated_into_shell():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github/workflows/skill-eval-cloud-replay.yml").read_text()
    workflow = yaml.safe_load(text)
    assert set(workflow[True]) == {"workflow_dispatch"}
    job = workflow["jobs"]["replay"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["permissions"] == {"contents": "read"}
    for step in job["steps"]:
        assert "${{ inputs." not in step.get("run", "")
    assert "env -i" in text and "secrets." not in text and "sysctl" not in text
