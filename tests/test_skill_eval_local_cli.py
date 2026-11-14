"""CLI handoff controls, not a local-agent run or evidence of skill efficacy.

Exercise the real argument parser/dispatch and retained files. The verifier is
an explicitly synthetic seam: these tests certify neither sandbox isolation nor
the behavior of a model-produced parser. No agent, provider or hardware runs.
"""

import json
import shutil
import sys

import pytest

from inferrsi.apps import skill_eval_engineering as artifact_check
from inferrsi.benchmark import skill_eval_engineering as engineering
from inferrsi.harness import artifact_sandbox as sandbox
from inferrsi.infra.artifacts import sha256, write_json
from tests.fixtures.skill_eval_compare_support import configuration
from tools import skill_eval_compare as compare

CORRECT_CONTROL = "# hand-authored synthetic correct control; no model execution\n"


def cli(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["skill_eval_compare", *map(str, args)])
    compare.main()
    stdout = capsys.readouterr().out
    return json.loads(stdout) if stdout else None


def prepare_cli(tmp_path, monkeypatch, capsys):
    # A routing regression must fail this test, never start the automatic client.
    def forbidden_execute(*args, **kwargs):
        pytest.fail("The manual CLI handoff must not launch an agent")

    monkeypatch.setattr(compare, "execute", forbidden_execute)
    config_path, repo = configuration(tmp_path, ids=("parser",), repeats=1)
    config = json.loads(config_path.read_text())
    config["adapter"] = engineering.ADAPTER
    write_json(config_path, config)
    write_json(tmp_path / "cases.json", [{
        "id": "parser", "split": "development", "request": engineering.REQUEST,
        "provenance": "synthetic CLI software control, not an agent trial",
    }])
    output = tmp_path / "run"
    prepared = cli(monkeypatch, capsys, "prepare", "--output", output,
                   "--config", config_path, "--repo", repo)
    assert prepared == {"plan_sha256": sha256(output / "plan/freeze.json")}
    head = config["snapshots"]["candidate"]["git_revision"]
    return output, prepared["plan_sha256"], head


def handoff_args(output, digest, head, role):
    return ("execute", "--output", output, "--authorize", digest,
            "--task-id", "parser", "--condition", role, "--repeat", "1",
            "--current-head", head)


@pytest.mark.parametrize("sandbox_unavailable", [False, True])
def test_cli_two_arm_capture_and_report_keep_synthetic_provenance(
        tmp_path, monkeypatch, capsys, sandbox_unavailable):
    output, digest, head = prepare_cli(tmp_path, monkeypatch, capsys)
    verified_sources = []

    def synthetic_verify(task, answer):
        engineering.validate_task(task)
        verified_sources.append(answer["artifact_source"])
        if sandbox_unavailable:
            raise sandbox.SandboxUnavailable("synthetic unavailable-host control")
        passed = answer["artifact_source"] == CORRECT_CONTROL
        return {"passed": passed, "assertions": {"synthetic_cli_control": passed}}

    monkeypatch.setattr(artifact_check, "verify", synthetic_verify)
    plan = compare.load_plan(output)
    retained_sources = []
    for role in compare.ROLES:
        workspace = tmp_path / role
        args = handoff_args(output, digest, head, role)
        cli(monkeypatch, capsys, *args, "--stage-local", workspace)
        if role == "candidate":
            (workspace / "parser.py").write_text(CORRECT_CONTROL, encoding="utf-8")
        source = (workspace / "parser.py").read_bytes()
        retained_sources.append(source.decode("utf-8"))
        logs = tmp_path / (role + "-submission")
        logs.mkdir()
        metadata = {
            "model": plan["model"], "environment": "synthetic CLI fixture",
            "access": "hand-authored control; no actual agent or hardware run",
            "resources": "no provider calls or charges; not model usage evidence",
            "usage": None, "elapsed_seconds": 1.0, "agent_exit_code": 0,
            "summary": "synthetic software control only: " + role,
        }
        write_json(logs / "execution.json", metadata)
        (logs / "stdout.log").write_text(role + " synthetic author log\n", encoding="utf-8")
        (logs / "stderr.log").write_bytes(b"")
        cli(monkeypatch, capsys, *args, "--capture-local", workspace,
            "--submission", logs)
        entry = next(entry for entry in plan["schedule"] if entry["condition"] == role)
        directory = compare.trial_directory(output, entry)
        assert (directory / "parser.py").read_bytes() == source
        assert json.loads((directory / "author-execution.json").read_text()) == metadata
        for name in ("stdout.log", "stderr.log"):
            assert (directory / "agent" / name).read_bytes() == (logs / name).read_bytes()
        assert json.loads((directory / "receipt.json").read_text()) == {
            "result_sha256": sha256(directory / "result.json")}

    retained = {p: p.read_bytes() for p in (output / "results").rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="already started"):
        cli(monkeypatch, capsys, *args, "--capture-local", workspace, "--submission", logs)
    assert all(p.read_bytes() == value for p, value in retained.items())
    for role in compare.ROLES:
        shutil.rmtree(tmp_path / role)
        shutil.rmtree(tmp_path / (role + "-submission"))
    # The report must use the retained closure, not the now-absent live workspaces.
    before_replay = len(verified_sources)
    assert cli(monkeypatch, capsys, "report", "--output", output) is None
    result = json.loads((output / "report.json").read_text())
    assert retained == {p: p.read_bytes() for p in (output / "results").rglob("*")
                        if p.is_file()}
    assert sorted(verified_sources[before_replay:]) == sorted(retained_sources)
    assert result["status"]["execution_state"] == "author_reported_unverified"
    assert "do not prove skill causality" in result["claim"]
    pair = result["pairs"][0]
    assert pair["comparability"] == "unverified_author_reported"
    for role in compare.ROLES:
        trial = pair[role]
        assert trial["author_reported_execution"] is True
        assert trial["observations"]["usage"] is None
        assert "costs not independently verified" in trial["provenance"]
        if sandbox_unavailable:
            assert trial["verdict"] == "INCONCLUSIVE"
            assert trial["artifact_verdict"] is None
            assert trial["evaluation_error"] == "artifact_sandbox_unavailable"
        else:
            assert trial["artifact_verdict"] == ("PASS" if role == "candidate" else "FAIL")
            assert trial["grader_trust"] == "reverified"
            assert trial["artifact_checks"]["assertions"] == {
                "synthetic_cli_control": role == "candidate"}
    assert result["counts"]["artifact_improved"] == (0 if sandbox_unavailable else 1)
    assert not (output / "results/start.json").exists()
    assert "do not authorize merge" in (output / "report.md").read_text()


def test_cli_rejects_stale_head_and_incomplete_capture_before_writing(
        tmp_path, monkeypatch, capsys):
    output, digest, head = prepare_cli(tmp_path, monkeypatch, capsys)
    workspace = tmp_path / "baseline"
    args = handoff_args(output, digest, head, "baseline")
    with pytest.raises(ValueError, match="head differs"):
        cli(monkeypatch, capsys, *handoff_args(output, digest, "0" * 40, "baseline"),
            "--stage-local", workspace)
    assert not workspace.exists()
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, capsys, *args[:-2], "--stage-local", workspace)
    assert error.value.code == 2
    assert "--current-head" in capsys.readouterr().err
    assert not workspace.exists()
    cli(monkeypatch, capsys, *args, "--stage-local", workspace)
    before = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="head differs"):
        cli(monkeypatch, capsys, *handoff_args(output, digest, "0" * 40, "baseline"),
            "--capture-local", workspace, "--submission", tmp_path / "not-submitted")
    assert {p: p.read_bytes() for p in output.rglob("*") if p.is_file()} == before
    with pytest.raises(SystemExit) as error:
        cli(monkeypatch, capsys, *args, "--capture-local", workspace)
    assert error.value.code == 2
    assert "Capture requires --submission" in capsys.readouterr().err
    assert {p: p.read_bytes() for p in output.rglob("*") if p.is_file()} == before
