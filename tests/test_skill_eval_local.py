"""Local handoff software controls; fake grader results are not agent efficacy."""
import json

import pytest

from inferrsi.apps import skill_eval_engineering as artifact_check
from inferrsi.benchmark import skill_eval_engineering as engineering
from inferrsi.harness import artifact_sandbox as sandbox
from inferrsi.infra.artifacts import sha256, write_json
from tests.fixtures.skill_eval_compare_support import configuration
from tools import skill_eval_compare as compare
from tools import skill_eval_local as local


def prepared(tmp_path):
    config_path, repo = configuration(tmp_path, ids=("parser",), repeats=1)
    config = json.loads(config_path.read_text())
    config["adapter"] = engineering.ADAPTER
    write_json(config_path, config)
    write_json(tmp_path / "cases.json", [{"id": "parser", "split": "development",
                                        "request": engineering.REQUEST,
                                        "provenance": "public reconstructed test fixture"}])
    output = tmp_path / "run"
    digest = compare.prepare(config_path, repo, output)
    return output, digest, config["snapshots"]["candidate"]["git_revision"]


def submission(tmp_path, output):
    root = tmp_path / "author-logs"
    root.mkdir()
    plan = compare.load_plan(output)
    write_json(root / "execution.json", {
        "model": plan["model"], "environment": "offline test fixture",
        "access": "not an actual agent run", "resources": "no model charges",
        "usage": None, "elapsed_seconds": 1.0, "agent_exit_code": 0,
        "summary": "software fixture only",
    })
    (root / "stdout.log").write_text("author-supplied fixture log\n")
    (root / "stderr.log").write_text("")
    return root


def fake_verify(task, answer):
    engineering.validate_task(task)
    passed = answer["artifact_source"] == "# authored correct fixture\n"
    return {"passed": passed, "assertions": {"software_fixture": passed}}


def test_manual_handoff_keeps_artifact_and_author_provenance_separate(tmp_path, monkeypatch):
    output, digest, head = prepared(tmp_path)
    monkeypatch.setattr(artifact_check, "verify", fake_verify)
    logs = submission(tmp_path, output)
    for role in compare.ROLES:
        workspace = tmp_path / role
        local.stage(output, digest, "parser", role, 1, workspace, head)
        if role == "candidate":
            (workspace / "parser.py").write_text("# authored correct fixture\n")
        result = local.capture(output, digest, "parser", role, 1, workspace, head, logs)
    assert result["counts"]["artifact_improved"] == 1
    assert "do not prove skill causality" in result["claim"]
    pair = result["pairs"][0]
    assert pair["baseline"]["verdict"] == "FAIL"
    assert pair["candidate"]["verdict"] == "PASS"
    assert pair["candidate"]["author_reported_execution"] is True
    assert pair["candidate"]["grader_trust"] == "reverified"
    assert "parser.py" in pair["candidate"]["evidence"]
    assert "author-execution.json" in pair["candidate"]["evidence"]
    assert pair["candidate"]["observations"]["usage"] is None
    assert pair["candidate"]["observations"]["tool_calls"] is None
    assert "Observed command count: unknown" in compare.markdown(result)
    assert not (output / "results/start.json").exists()
    with pytest.raises(ValueError, match="already started"):
        local.capture(output, digest, "parser", "candidate", 1, tmp_path / "candidate", head, logs)


@pytest.mark.parametrize("mutation", ["missing", "input", "additional", "symlink"])
def test_capture_rejects_missing_or_changed_frozen_inputs(tmp_path, monkeypatch, mutation):
    output, digest, head = prepared(tmp_path)
    monkeypatch.setattr(artifact_check, "verify", fake_verify)
    workspace = tmp_path / "baseline"
    local.stage(output, digest, "parser", "baseline", 1, workspace, head)
    if mutation == "missing":
        (workspace / "parser.py").unlink()
    elif mutation == "input":
        (workspace / "task.json").write_text("{}")
    elif mutation == "additional":
        (workspace / "extra").mkdir()
        (workspace / "extra/parser.py").write_text("not an allowed edit")
    else:
        (workspace / "parser.py").unlink()
        (workspace / "parser.py").symlink_to(tmp_path / "absent")
    result = local.capture(output, digest, "parser", "baseline", 1, workspace, head,
                           submission(tmp_path, output))
    assert result["pairs"][0]["baseline"]["verdict"] == "INCONCLUSIVE"


def test_stale_head_and_missing_logs_are_not_accepted(tmp_path):
    output, digest, head = prepared(tmp_path)
    workspace = tmp_path / "baseline"
    with pytest.raises(ValueError, match="head differs"):
        local.stage(output, digest, "parser", "baseline", 1, workspace, "0" * 40)
    local.stage(output, digest, "parser", "baseline", 1, workspace, head)
    logs = submission(tmp_path, output)
    (logs / "stdout.log").unlink()
    with pytest.raises(ValueError, match="Supply execution.json"):
        local.capture(output, digest, "parser", "baseline", 1, workspace, head, logs)


@pytest.mark.parametrize("commands, completed_turns, expected", [
    (None, None, None), ([], None, None), ([], 0, 0), ([{"synthetic": True}], 1, 1),
])
def test_import_command_count_preserves_unknown_and_observed_zero(
        tmp_path, monkeypatch, commands, completed_turns, expected):
    from inferrsi.harness.usage import usage_totals

    output, digest, head = prepared(tmp_path)
    monkeypatch.setattr(artifact_check, "verify", fake_verify)
    workspace = tmp_path / "baseline"
    local.stage(output, digest, "parser", "baseline", 1, workspace, head)
    original = local.capture(output, digest, "parser", "baseline", 1, workspace, head,
                             submission(tmp_path, output))
    path = output / original["pairs"][0]["baseline"]["evidence"]["result.json"]
    record = compare.read_json(path)
    assert record["observations"]["commands"] is None
    # Synthetic legacy/unparsed and explicitly parsed controls, not real author logs.
    record["observations"].update(commands=commands, completed_turns=completed_turns)
    write_json(path, record)
    receipt = path.parent / "receipt.json"
    write_json(receipt, {"result_sha256": sha256(path)})
    saved = path.read_bytes(), receipt.read_bytes()
    result = compare.read_report(output)
    trial = result["pairs"][0]["baseline"]
    assert trial["observations"]["tool_calls"] == expected
    assert usage_totals([trial])["tool_calls"] == expected
    assert usage_totals([trial, {**trial, "observations": {"tool_calls": None}}])[
        "tool_calls"] is None
    rendered = "unknown" if expected is None else str(expected)
    assert "Observed command count: " + rendered in compare.markdown(result)
    assert saved == (path.read_bytes(), receipt.read_bytes())


def test_unavailable_sandbox_remains_inconclusive(tmp_path, monkeypatch):
    output, digest, head = prepared(tmp_path)
    workspace = tmp_path / "baseline"
    local.stage(output, digest, "parser", "baseline", 1, workspace, head)

    def unavailable(*_):
        raise sandbox.SandboxUnavailable("mocked unsupported host")

    monkeypatch.setattr(artifact_check, "verify", unavailable)
    result = local.capture(output, digest, "parser", "baseline", 1, workspace, head,
                           submission(tmp_path, output))
    trial = result["pairs"][0]["baseline"]
    assert trial["verdict"] == "INCONCLUSIVE"
    assert trial["evaluation_error"] == "artifact_sandbox_unavailable"


def test_no_unsafe_default_agent_launch(tmp_path):
    output, digest, _ = prepared(tmp_path)
    with pytest.raises(ValueError, match="confined local client"):
        compare.execute(output, digest)


def test_retained_artifact_tamper_and_missing_author_evidence(tmp_path, monkeypatch):
    output, digest, head = prepared(tmp_path)
    monkeypatch.setattr(artifact_check, "verify", fake_verify)
    workspace = tmp_path / "baseline"
    local.stage(output, digest, "parser", "baseline", 1, workspace, head)
    result = local.capture(output, digest, "parser", "baseline", 1, workspace, head,
                           submission(tmp_path, output))
    artifact = output / result["pairs"][0]["baseline"]["evidence"]["parser.py"]
    artifact.write_text("# substituted source")
    result = compare.read_report(output)
    assert result["pairs"][0]["baseline"]["evaluation_error"] == "retained_artifact_hash_mismatch"


def test_rewritten_stage_receipt_cannot_authorize_changed_inputs(tmp_path, monkeypatch):
    output, digest, head = prepared(tmp_path)
    monkeypatch.setattr(artifact_check, "verify", fake_verify)
    workspace = tmp_path / "baseline"
    local.stage(output, digest, "parser", "baseline", 1, workspace, head)
    (workspace / "task.json").write_text("{}")
    directory = compare.trial_directory(output, compare.load_plan(output)["schedule"][0])
    receipt = json.loads((directory / "local-stage.json").read_text())
    receipt["input_hashes"]["task.json"] = sha256(workspace / "task.json")
    write_json(directory / "local-stage.json", receipt)
    result = local.capture(output, digest, "parser", "baseline", 1, workspace, head,
                           submission(tmp_path, output))
    assert result["pairs"][0]["baseline"]["evaluation_error"] == "inputs_changed"


@pytest.mark.parametrize("field,value,error", [
    ("agent_exit_code", 1, "reported_agent_failure"),
    ("elapsed_seconds", 61, "reported_run_exceeded_frozen_timeout"),
])
def test_valid_artifact_does_not_erase_failed_or_overbudget_author_run(
        tmp_path, monkeypatch, field, value, error):
    output, digest, head = prepared(tmp_path)
    monkeypatch.setattr(artifact_check, "verify", fake_verify)
    workspace = tmp_path / "baseline"
    local.stage(output, digest, "parser", "baseline", 1, workspace, head)
    (workspace / "parser.py").write_text("# authored correct fixture\n")
    logs = submission(tmp_path, output)
    metadata = json.loads((logs / "execution.json").read_text())
    metadata[field] = value
    write_json(logs / "execution.json", metadata)
    result = local.capture(output, digest, "parser", "baseline", 1, workspace, head, logs)
    trial = result["pairs"][0]["baseline"]
    assert trial["verdict"] == "INCONCLUSIVE"
    assert trial["artifact_verdict"] == "PASS"
    assert trial["evaluation_error"] == error


def test_retained_symlink_is_never_read_as_artifact_evidence(tmp_path, monkeypatch):
    output, digest, head = prepared(tmp_path)
    monkeypatch.setattr(artifact_check, "verify", fake_verify)
    workspace = tmp_path / "baseline"
    local.stage(output, digest, "parser", "baseline", 1, workspace, head)
    result = local.capture(output, digest, "parser", "baseline", 1, workspace, head,
                           submission(tmp_path, output))
    artifact = output / result["pairs"][0]["baseline"]["evidence"]["parser.py"]
    artifact.unlink()
    target = tmp_path / "not-evidence.txt"
    target.write_text("must not appear in report")
    artifact.symlink_to(target)
    result = compare.read_report(output)
    trial = result["pairs"][0]["baseline"]
    assert trial["evaluation_error"] == "unsafe_retained_file"
    assert trial["output"] is None
    assert "must not appear" not in json.dumps(result)


def test_retained_artifact_preserves_crlf_bytes(tmp_path, monkeypatch):
    output, digest, head = prepared(tmp_path)
    monkeypatch.setattr(artifact_check, "verify", fake_verify)
    workspace = tmp_path / "baseline"
    local.stage(output, digest, "parser", "baseline", 1, workspace, head)
    (workspace / "parser.py").write_bytes(b"# CRLF artifact\r\n")
    result = local.capture(output, digest, "parser", "baseline", 1, workspace, head,
                           submission(tmp_path, output))
    trial = result["pairs"][0]["baseline"]
    assert trial["verdict"] == "FAIL"  # Fake grader intentionally rejects this source.
    assert trial["evaluation_error"] is None


@pytest.mark.parametrize("target", ["execution.json", "author-execution.json", "answer.json",
                                    "parser.py", "agent", "verification", "receipt.json"])
def test_capture_refuses_preexisting_dangling_retained_target(tmp_path, monkeypatch, target):
    output, digest, head = prepared(tmp_path)
    monkeypatch.setattr(artifact_check, "verify", fake_verify)
    workspace = tmp_path / "baseline"
    local.stage(output, digest, "parser", "baseline", 1, workspace, head)
    directory = compare.trial_directory(output, compare.load_plan(output)["schedule"][0])
    outside = tmp_path / "must-not-be-created"
    (directory / target).symlink_to(outside)
    with pytest.raises(ValueError, match="reserved targets"):
        local.capture(output, digest, "parser", "baseline", 1, workspace, head,
                      submission(tmp_path, output))
    assert not outside.exists()


def test_report_replays_artifact_after_blocked_capture_without_rewriting_attempt(
        tmp_path, monkeypatch):
    output, digest, head = prepared(tmp_path)
    workspace = tmp_path / "baseline"
    local.stage(output, digest, "parser", "baseline", 1, workspace, head)
    (workspace / "parser.py").write_text("# authored correct fixture\n")

    def unavailable(*_):
        raise sandbox.SandboxUnavailable("capture host blocked")

    monkeypatch.setattr(artifact_check, "verify", unavailable)
    local.capture(output, digest, "parser", "baseline", 1, workspace, head,
                  submission(tmp_path, output))
    directory = compare.trial_directory(output, compare.load_plan(output)["schedule"][0])
    retained_record = (directory / "result.json").read_bytes()
    monkeypatch.setattr(artifact_check, "verify", fake_verify)
    result = compare.read_report(output)
    trial = result["pairs"][0]["baseline"]
    assert trial["artifact_verdict"] == "PASS"
    assert trial["artifact_checks"]["passed"] is True
    assert trial["checks"] is None
    assert trial["evaluation_error"] == "artifact_sandbox_unavailable"
    assert trial["verdict"] == "INCONCLUSIVE"
    assert result["pairs"][0]["comparability"] == "unverified_author_reported"
    assert (directory / "result.json").read_bytes() == retained_record


@pytest.mark.parametrize("tamper", [False, True])
def test_stage_frozen_inputs_ignore_host_newline_translation(tmp_path, monkeypatch, tamper):
    """Simulate Windows text writes; this is not native Windows sandbox coverage."""
    from pathlib import Path

    output, digest, head = prepared(tmp_path)
    logs = submission(tmp_path, output)
    original_write_text = Path.write_text

    def crlf_write_text(self, data, encoding=None, errors=None, newline=None):
        return original_write_text(self, data, encoding=encoding, errors=errors,
                                   newline="\r\n" if newline is None else newline)

    monkeypatch.setattr(Path, "write_text", crlf_write_text)
    monkeypatch.setattr(artifact_check, "verify", fake_verify)
    workspace = tmp_path / "baseline"
    local.stage(output, digest, "parser", "baseline", 1, workspace, head)
    for name in ("task.json", "INSTRUCTIONS.txt"):
        assert b"\r\n" not in (workspace / name).read_bytes()
    if tamper:
        path = workspace / "INSTRUCTIONS.txt"
        path.write_bytes(path.read_bytes() + b"tampered\n")
    (workspace / "parser.py").write_bytes(b"# authored correct fixture\n")
    result = local.capture(output, digest, "parser", "baseline", 1, workspace, head, logs)
    trial = result["pairs"][0]["baseline"]
    assert trial["evaluation_error"] == ("inputs_changed" if tamper else None)
    assert trial["verdict"] == ("INCONCLUSIVE" if tamper else "PASS")
