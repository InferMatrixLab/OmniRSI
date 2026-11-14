"""No real sandbox/model is required for qualification orchestration tests."""
import ast
import json
from unittest.mock import patch

import pytest
import yaml

from tools import skill_eval_qualify as qualify


def controls():
    checks = {vector[0]: True for vector in qualify.engineering.VECTORS}
    return [{"passed": True, "assertions": checks},
            {"passed": False, "assertions": {**checks, "stream_error": False}}]


def test_qualification_requires_both_controls():
    with patch.object(qualify.shutil, "which", return_value=None), \
         patch.object(qualify.artifact_check, "verify", side_effect=controls()) as verify:
        result = qualify.qualify("a" * 40, "test-image")
    assert result["verdict"] == "PASS"
    assert verify.call_count == 2


def test_unavailable_is_inconclusive_not_skipped():
    with patch.object(qualify.shutil, "which", return_value=None), \
         patch.object(qualify.artifact_check, "verify",
                      side_effect=qualify.artifact_sandbox.SandboxUnavailable("blocked")):
        result = qualify.qualify("a" * 40, "test-image")
    assert result["verdict"] == "INCONCLUSIVE"
    assert result["error"] == "blocked"


def test_cli_retains_inconclusive_and_returns_failure(tmp_path):
    output = tmp_path / "receipt"
    with patch("sys.argv", ["qualify", "--output", str(output), "--revision", "a" * 40,
                            "--runner-image", "test"]), \
         patch.object(qualify, "qualify", return_value={"verdict": "INCONCLUSIVE"}):
        assert qualify.main() == 1
        assert json.loads((output / "qualification.json").read_text())["verdict"] == "INCONCLUSIVE"
        with pytest.raises(FileExistsError):
            qualify.main()


def test_floating_revision_rejected():
    with pytest.raises(ValueError, match="exact"):
        qualify.qualify("main", "test-image")


@pytest.mark.parametrize("change", ["good_failed", "bad_passed", "missing_check"])
def test_control_mismatch_fails(change):
    results = controls()
    if change == "good_failed":
        results[0]["passed"] = False
    elif change == "bad_passed":
        results[1]["passed"] = True
    else:
        results[0]["assertions"].pop("length")
    with patch.object(qualify.shutil, "which", return_value=None), \
         patch.object(qualify.artifact_check, "verify", side_effect=results):
        assert qualify.qualify("a" * 40, "test-image")["verdict"] == "FAIL"


def test_control_is_existing_reviewed_fixture():
    tree = ast.parse((qualify.ROOT / "tests/test_skill_eval_engineering.py").read_text())
    source = next(ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign) and any(
                      isinstance(target, ast.Name) and target.id == "GOOD_ARTIFACT"
                      for target in node.targets))
    assert qualify.CONTROL.read_text() == source


def test_workflow_is_manual_read_only_and_clear_environment():
    text = (qualify.ROOT / ".github/workflows/skill-eval-qualify.yml").read_text()
    workflow = yaml.safe_load(text)
    # PyYAML's YAML 1.1 loader treats `on` as True.
    assert workflow[True] == {"workflow_dispatch": None}
    assert workflow["permissions"] == {}
    job = workflow["jobs"]["qualify"]
    assert job["if"] == "github.ref == 'refs/heads/main'"
    assert job["permissions"] == {"contents": "read"}
    assert job["runs-on"] == "ubuntu-22.04"
    assert job["steps"][0]["with"]["persist-credentials"] is False
    command = job["steps"][2]["run"]
    assert "env -i PATH=/usr/bin:/bin LANG=C.UTF-8 PYTHONPATH=src" in command
    assert "secrets." not in text and "pull_request_target" not in text
    assert "sysctl" not in text and "continue-on-error" not in text
