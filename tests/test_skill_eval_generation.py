"""Offline generation-only controller tests; no provider or artifact execution."""
import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest

from inferrsi.apps import skill_eval_engineering as artifact_check
from inferrsi.apps import skill_evaluation as evaluation
from inferrsi.benchmark import skill_eval_engineering as engineering
from inferrsi.harness import artifact_sandbox as sandbox
from inferrsi.harness import skill_evaluation as harness
from inferrsi.infra.artifacts import sha256, write_json
from inferrsi.model import anthropic_agent as agent
from tests.fixtures.skill_eval_compare_support import configuration
from tools import skill_eval_compare as compare

TASK = {"id": "parser", "split": "development", "request": engineering.REQUEST,
        "provenance": "Reconstructed CPU public fixture"}
SOURCE = "def evaluate(status, frames, requested_tokens):\n    return {}\n"
ANSWER = {"artifact": "parser.py", "summary": "Generated, not executed", "artifact_source": SOURCE}
SPEC = {"kind": agent.FULL_CONTEXT_KIND, "provider": "yibuapi", "endpoint": agent.ENDPOINT,
        "api_version": "2023-06-01", "max_requests": 1, "max_output_tokens": 4096,
        "max_request_bytes": 262144, "max_read_bytes": 32768, "max_tool_calls": 12}


def prepared(tmp_path):
    config, repo = configuration(tmp_path, ids=("parser",))
    data = compare.read_json(config)
    data.update(adapter=engineering.ADAPTER, execution_mode=engineering.GENERATION_MODE,
                model={"name": "claude-opus-5-5", "effort": "not_applicable"}, client=SPEC)
    write_json(config, data)
    write_json(tmp_path / "cases.json", [TASK])
    output = tmp_path / "evidence"
    digest = compare.prepare(config, repo, output)
    return data, output, digest


def test_freezes_explicit_mode_schema_starter_and_two_identical_client_conditions(tmp_path):
    _, output, digest = prepared(tmp_path)
    plan = compare.load_plan(output)
    assert plan["execution_mode"] == engineering.GENERATION_MODE
    assert plan["prompt"] == engineering.GENERATION_PROMPT
    assert compare.read_json(output / "plan/schema.json") == engineering.GENERATION_SCHEMA
    assert (output / "plan/starter/parser.py").read_bytes() == engineering.STARTER.encode()
    assert plan["files"]["plan/starter/parser.py"] == sha256(output / "plan/starter/parser.py")
    assert plan["max_api_requests"] == 2
    assert plan["client"]["settings"] == SPEC  # one shared configuration, not arm-specific
    report = compare.report(output)
    assert report["execution_mode"] == engineering.GENERATION_MODE
    assert "not measure iterative" in report["claim"]
    with pytest.raises(ValueError, match="pricing/cap"):
        compare.execute(output, digest)
    assert not list((output / "results").iterdir())


@pytest.mark.parametrize("change", [
    {"execution_mode": "unknown"}, {"adapter": "audit"}, {"client": None},
    {"client": {**SPEC, "kind": agent.KIND, "max_requests": 2}},
])
def test_mode_never_silently_falls_back(tmp_path, change):
    data, _, _ = prepared(tmp_path)
    data.update(change)
    if data.get("client") is None:
        data.pop("client", None)
    with pytest.raises(ValueError):
        compare.validate(data, [TASK])


def test_legacy_engineering_still_rejects_provider(tmp_path):
    data, _, _ = prepared(tmp_path)
    data.pop("execution_mode")
    with pytest.raises(ValueError, match="require local execution"):
        compare.validate(data, [TASK])


def materialize(root, answer):
    return sandbox.materialize_generated_artifact(
        root, answer, engineering.STARTER.encode(), engineering.MAX_SOURCE_BYTES)


def workspace(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    write_json(root / "task.json", TASK)
    (root / "parser.py").write_bytes(engineering.STARTER.encode())
    (root / "knowledge").mkdir()
    (root / "knowledge/SKILL.md").write_text("assigned arm only")
    return root


def test_context_contains_full_starter_and_assigned_input_only(tmp_path):
    root = workspace(tmp_path)
    (tmp_path / "other-arm.txt").write_text("not exposed")
    payload, manifest = agent.full_context_payload(
        root, engineering.GENERATION_SCHEMA, engineering.GENERATION_PROMPT,
        "claude-opus-5-5", SPEC)
    assert {x["path"] for x in manifest} == {"task.json", "parser.py", "knowledge/SKILL.md"}
    assert [x["name"] for x in payload["tools"]] == ["submit_answer"]
    assert "not exposed" not in agent.canonical(payload)
    assert engineering.STARTER in [x["content"] for x in json.loads(
        payload["messages"][0]["content"].split("submission.\n", 1)[1])]


@pytest.mark.parametrize("change", [
    {"artifact": "../../outside.py"}, {"artifact_source": ""},
    {"artifact_source": "x" * 65537}, {"artifact_source": "é" * 32769},
    {"artifact_source": "\ud800"}, {"artifact_source": None}, {"summary": []},
    {"files": {"extra.py": "bad"}}, {"passed": True},
])
def test_materializer_rejects_bad_or_multiple_outputs_without_writing(tmp_path, change):
    root = workspace(tmp_path)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(ValueError):
        materialize(root, {**ANSWER, **change})
    assert before == {p.relative_to(root): p.read_bytes()
                      for p in root.rglob("*") if p.is_file()}


def test_materializer_requires_untouched_starter_and_exact_bytes(tmp_path):
    root = workspace(tmp_path)
    frozen = sandbox.input_hashes(root)
    assert materialize(root, ANSWER) == {
        "artifact": "parser.py", "summary": ANSWER["summary"]}
    assert (root / "parser.py").read_bytes() == SOURCE.encode()
    assert sandbox.input_hashes(root) == frozen
    with pytest.raises(ValueError, match="unchanged"):
        materialize(root, ANSWER)


@pytest.mark.parametrize("sandbox_available", [True, False])
def test_trial_retains_real_source_without_executing_it_on_host(tmp_path, sandbox_available):
    root = workspace(tmp_path)
    trial = tmp_path / "trial"

    class OfflineClient:
        timeout_seconds = 10

        def command(self, *args):
            return ("trusted-stub",)

        def trace_metrics(self, path):
            return {"usage": {"input_tokens": 7, "output_tokens": 3}, "commands": [],
                    "errors": [], "completed_turns": 1}

    def execute(command, cwd, folder, deadline):
        assert command == ("trusted-stub",)
        folder.mkdir(parents=True)
        (folder / "stdout.log").write_text("offline controller fixture")
        (folder / "stderr.log").write_text("")
        write_json(trial / "answer.json", ANSWER)
        return {"status": "completed", "returncode": 0, "elapsed_seconds": 0}

    checks = {"passed": False, "assertions": {"stream_error": False}}
    with mock.patch.object(harness, "execute_command", side_effect=execute), \
         mock.patch.object(artifact_check, "verify", return_value=checks,
                           side_effect=None if sandbox_available else
                           sandbox.SandboxUnavailable("unavailable")) as verify:
        record = evaluation.run_trial(OfflineClient(), TASK, "baseline", 1, root, trial,
                                   tmp_path / "schema", tmp_path / "verifier", "prompt",
                                   float("inf"), engineering_artifact=True, generation_only=True)
    verify.assert_not_called()
    assert (trial / "parser.py").read_bytes() == SOURCE.encode()
    assert record["execution_mode"] == engineering.GENERATION_MODE
    assert record["inputs_unchanged"]
    assert record["verdict"] == "INCONCLUSIVE"
    assert record["evaluation_error"] == "artifact_verification_deferred"


def test_remote_replay_preserves_generation_failure_and_adds_artifact_verdict():
    plan = {"execution_mode": engineering.GENERATION_MODE,
            "exam": {"grader": sha256(Path(engineering.__file__))}}
    trial = {"task_id": "parser", "execution_mode": engineering.GENERATION_MODE,
             "verdict": "INCONCLUSIVE", "evaluation_error": "artifact_verification_deferred",
             "checks": None, "output": json.dumps(ANSWER)}
    checks = {"passed": True, "assertions": {"fixture": True}}
    with mock.patch.object(artifact_check, "verify", return_value=checks):
        compare.verify_retained_grader(plan, [trial], {"parser": TASK})
    assert trial["verdict"] == "INCONCLUSIVE"
    assert trial["evaluation_error"] == "artifact_verification_deferred"
    assert trial["artifact_verdict"] == "PASS"
    with mock.patch.object(artifact_check, "verify", side_effect=sandbox.SandboxUnavailable()):
        compare.verify_retained_grader(plan, [trial], {"parser": TASK})
    assert trial["evaluation_error"] == "artifact_verification_deferred"
    assert trial["artifact_replay_error"] == "artifact_sandbox_unavailable"
    assert trial["artifact_verdict"] is None


def test_generation_execute_requires_request_broker_even_without_cli_flag(tmp_path):
    _, output, digest = prepared(tmp_path)
    with mock.patch.object(agent.AnthropicAgentClient, "require_live_readiness", return_value={}), \
         mock.patch.object(agent, "gate_environment", side_effect=RuntimeError("broker required")):
        with pytest.raises(RuntimeError, match="broker required"):
            compare.execute(output, digest)
    assert not list((output / "results").iterdir())


def test_cloud_capture_to_remote_report_preserves_error_and_shows_comparison(tmp_path):
    _, output, digest = prepared(tmp_path)

    def execute(command, cwd, folder, deadline, **kwargs):
        folder.mkdir(parents=True)
        (folder / "stdout.log").write_text("offline provider fixture")
        (folder / "stderr.log").write_text("")
        # This trusted fake writes a structured response only, never parser.py.
        source = SOURCE + ("# candidate\n" if "New body" in
                           (cwd / "knowledge/selected/SKILL.md").read_text() else "# baseline\n")
        write_json(folder.parent / "answer.json", {**ANSWER, "artifact_source": source})
        return {"status": "completed", "returncode": 0, "elapsed_seconds": 0}

    trace = {"usage": {"input_tokens": 7, "output_tokens": 3}, "commands": [],
             "errors": [], "completed_turns": 1, "client_kind": agent.FULL_CONTEXT_KIND,
             "provider_model": "claude-opus-5-5", "api_requests": 1,
             "usage_complete": True, "observed_usage": {"input_tokens": 7, "output_tokens": 3}}
    with mock.patch.object(agent.AnthropicAgentClient, "require_live_readiness", return_value={}), \
         mock.patch.object(agent.AnthropicAgentClient, "environment", return_value={}), \
         mock.patch.object(agent.AnthropicAgentClient, "trace_metrics", return_value=trace), \
         mock.patch.object(harness, "execute_command", side_effect=execute), \
         mock.patch.object(artifact_check, "verify",
                           side_effect=AssertionError("must not execute")) as verifier:
        initial = compare.execute(output, digest)
    verifier.assert_not_called()
    assert initial["status"]["recorded_trials"] == 2
    assert initial["pairs"][0]["outcome"] == "inconclusive"
    assert initial["pairs"][0]["replayed_artifact_outcome"] == "inconclusive"

    def verify(task, answer):
        passed = "# candidate" in answer["artifact_source"]
        return {"passed": passed, "assertions": {"stream_error": passed}}

    with mock.patch.object(artifact_check, "verify", side_effect=verify):
        replay = compare.report(output)
    pair = replay["pairs"][0]
    assert pair["outcome"] == "inconclusive"
    assert pair["comparability"] == "generation_only_provider"
    assert pair["replayed_artifact_outcome"] == "artifact_improved"
    assert pair["baseline"]["artifact_verdict"] == "FAIL"
    assert pair["candidate"]["artifact_verdict"] == "PASS"
    assert all(pair[role]["evaluation_error"] == "artifact_verification_deferred"
               for role in compare.ROLES)
    assert "artifact_improved" in (output / "report.md").read_text()
    from tools.skill_eval_cloud_export import export
    protocol = tmp_path / "protocol.md"
    protocol.write_text("Frozen prospective protocol")
    with pytest.raises(ValueError):
        export(output, protocol)
    frozen = compare.load_plan(output)
    write_json(output / "historical-execution-approval.json", {
        "protocol_sha256": sha256(protocol), "plan_sha256": digest,
        **{role: frozen["arms"][role]["git_revision"] for role in compare.ROLES}})
    bundle = export(output, protocol)
    assert bundle["schema_version"] == "engineering-cloud-replay-v1"
    assert [a["arm"] for a in bundle["artifacts"]] == ["baseline", "candidate"]
    assert all(a["sha256"] == hashlib.sha256(a["source"].encode()).hexdigest()
               for a in bundle["artifacts"])
    assert bundle["protocol_sha256"] == hashlib.sha256(protocol.read_bytes()).hexdigest()
    from tools.skill_eval_cloud_replay import validate_bundle
    raw = json.dumps(bundle).encode()
    assert validate_bundle(raw, hashlib.sha256(raw).hexdigest()) == bundle
    protocol.write_text("Changed after admission")
    with pytest.raises(ValueError, match="approval does not bind"):
        export(output, protocol)
    protocol.write_text("Frozen prospective protocol")
    # Retained errors never authorize replay after an integrity failure.
    entry = compare.load_plan(output)["schedule"][0]
    trial_dir = compare.trial_directory(output, entry)
    (trial_dir / "parser.py").write_text("tampered")
    with mock.patch.object(artifact_check, "verify", side_effect=verify):
        tampered = compare.report(output)
    assert tampered["pairs"][0]["replayed_artifact_outcome"] == "inconclusive"
    with pytest.raises(ValueError, match="invalid retained"):
        export(output, protocol)
