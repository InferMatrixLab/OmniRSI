import importlib.util
import json
import time
from collections import Counter
from pathlib import Path

import pytest

from inferrsi.apps.skill_evaluation import export_bundle, run_trial, summarize
from inferrsi.benchmark.skill_evaluation import task_verdict
from inferrsi.harness.skill_evaluation import CodexClient, toml_value, trace_metrics
from inferrsi.knowledge_operations import KnowledgeReader

ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_codex_adapter_uses_ephemeral_isolated_real_command(tmp_path):
    command = CodexClient("test-model", "xhigh", 10).command(
        tmp_path / "workspace", tmp_path / "answer.json", tmp_path / "schema.json", "Task"
    )
    assert command[:2] == ("codex", "exec")
    assert "--ephemeral" in command and "--ignore-user-config" in command
    assert 'approval_policy="never"' in command
    assert 'sandbox_mode="danger-full-access"' in command
    assert 'web_search="disabled"' in command
    assert "--sandbox" not in command


def test_trace_retains_unknown_usage_and_errors(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"turn.failed","error":{"message":"limited"}}\n')
    trace = trace_metrics(path)
    assert trace["usage"] is None
    assert trace["completed_turns"] == 0 and trace["errors"]


@pytest.mark.parametrize("usage", ["malformed", [1]])
def test_malformed_usage_is_unknown_in_trace_and_legacy_summary(tmp_path, usage):
    path = tmp_path / "events.jsonl"
    raw = json.dumps({"type": "turn.completed", "usage": usage}).encode() + b"\n"
    path.write_bytes(raw)
    trace = trace_metrics(path)
    assert trace["usage"] is None and trace["completed_turns"] == 1
    assert path.read_bytes() == raw
    legacy = {"task_id": "one", "repeat": 1, "condition": "N", "verdict": "PASS",
              "elapsed_seconds": 1, "observations": {"usage": usage}}
    group = summarize([legacy])["conditions"]["N"]
    assert group["input_tokens"] is None and group["output_tokens"] is None
    # This is only the observed lower bound, never a claim of complete zero cost.
    assert load_tool("skill_eval_pilot").observed_tokens(usage) == 0
    assert legacy["observations"]["usage"] == usage


@pytest.mark.parametrize("invalid", ["17", [17], True, -17, 1.5])
def test_invalid_token_fields_keep_valid_partial_observations(tmp_path, invalid):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": invalid, "output_tokens": 17, "cached_input_tokens": invalid}}))
    usage = trace_metrics(path)["usage"]
    assert usage == {"input_tokens": None, "output_tokens": 17, "cached_input_tokens": None}
    assert load_tool("skill_eval_pilot").observed_tokens(usage) == 17


@pytest.mark.parametrize("status", ["timeout", "error", "cancelled", "failed"])
def test_incomplete_execution_never_passes(status):
    assert (
        task_verdict({"status": status}, {"completed_turns": 1, "errors": []}, {}, {"passed": True})
        == "INCONCLUSIVE"
    )


def test_completed_wrong_answer_fails_and_unavailable_grading_is_inconclusive():
    trace = {"completed_turns": 1, "errors": []}
    checks = {"passed": False, "assertions": {"schema": False}}
    assert task_verdict({"status": "completed"}, trace, {}, checks) == "FAIL"
    assert task_verdict({"status": "completed"}, trace, None, None) == "INCONCLUSIVE"
    assert task_verdict({"status": "completed"}, trace, {}, None) == "INCONCLUSIVE"


def test_export_preserves_declared_cross_skill_resource(tmp_path):
    reader = KnowledgeReader(skill_root=ROOT / "src/inferrsi/knowledge/data/skill_library")
    view = export_bundle(reader, "checkout:inference-benchmark-design", tmp_path)
    assert view["revision"].startswith("sha256:")
    assert (tmp_path / view["entrypoint"]).is_file()
    for path in view["dependencies"]:
        assert (tmp_path / path).is_file()
    assert not list(tmp_path.rglob(".git"))
    assert not list(tmp_path.rglob("verify.py"))


def test_pairs_preserve_regressions_and_missing_metrics():
    base = {
        "task_id": "one",
        "repeat": 1,
        "elapsed_seconds": 2,
        "observations": {"usage": None},
        "condition": "N",
        "verdict": "PASS",
    }
    result = summarize([base, {**base, "condition": "E-benchmark", "verdict": "FAIL"}])
    assert result["pairs"][0]["regression"] is True
    assert result["conditions"]["N"]["input_tokens"] is None
    assert result["conditions"]["N"]["cost"] is None


def test_toml_literals_do_not_shell_interpolate():
    assert toml_value({"key": [False, "a\\b"]}) == '{"key"=[false,"a\\\\b"]}'


def test_pilot_verifier_rejects_blank_answers():
    verifier = load_tool("skill_eval_verifier")
    pilot = load_tool("skill_eval_pilot")
    tasks = pilot.fixtures()
    assert len(tasks) == 6
    for task in tasks:
        assert not verifier.verify(task, {})["passed"]


def test_task_inputs_never_embed_oracle_fields():
    for task in load_tool("skill_eval_pilot").fixtures():
        assert "expected" not in task and "report_verdict" not in task
        assert task["provenance"].startswith("Historical latency slices")


def test_recovery_keeps_interrupted_attempt_without_automatic_retry(tmp_path):
    pilot = load_tool("skill_eval_pilot")
    entry = {"task_id": "task", "condition": "N", "repeat": 1}
    directory = tmp_path / "results/task-N-1"
    directory.mkdir(parents=True)
    trials = pilot.recover_attempts(tmp_path, [entry])
    assert trials[0]["execution_state"] == "interrupted"
    assert trials[0]["verdict"] == "INCONCLUSIVE"
    result_path = directory / "result.json"
    persisted = result_path.read_bytes()
    assert json.loads(persisted)["evaluation_error"] == "interrupted_attempt"
    group = summarize(trials)["conditions"]["N"]
    assert group["verdicts"] == {"PASS": 0, "FAIL": 0, "INCONCLUSIVE": 1}
    assert group["evaluation_errors"] == 1
    assert group["elapsed_seconds"] is None
    assert pilot.recover_attempts(tmp_path, [entry]) == trials
    assert result_path.read_bytes() == persisted


def test_recovery_preserves_legacy_record_without_evaluation_error(tmp_path):
    pilot = load_tool("skill_eval_pilot")
    entry = {"task_id": "legacy", "condition": "N", "repeat": 1}
    legacy = {
        **entry,
        "execution_state": "interrupted",
        "verdict": "INCONCLUSIVE",
        "elapsed_seconds": None,
        "observations": {"usage": None},
    }
    directory = tmp_path / "results/legacy-N-1"
    directory.mkdir(parents=True)
    result_path = directory / "result.json"
    persisted = json.dumps(legacy, indent=2).encode("utf-8") + b"\n"
    result_path.write_bytes(persisted)
    trials = pilot.recover_attempts(tmp_path, [entry])
    assert trials == [legacy]
    assert "evaluation_error" not in trials[0]
    group = summarize(trials)["conditions"]["N"]
    assert group["verdicts"]["INCONCLUSIVE"] == 1
    assert group["evaluation_errors"] == 0
    assert result_path.read_bytes() == persisted


def test_verifier_rejects_malformed_generated_answers():
    verifier = load_tool("skill_eval_verifier")
    task = load_tool("skill_eval_pilot").fixtures()[0]
    for answer in (
        [],
        {"findings": [[1]], "evidence": ["contract"]},
        {"findings": [], "evidence": [None]},
    ):
        assert not verifier.verify(task, answer)["passed"]


def test_final_fixture_protocol_is_explicit_and_uses_distinct_latency_inputs():
    pilot = load_tool("skill_eval_pilot")
    original, final = pilot.fixtures(), pilot.fixtures(True)
    assert original[0]["baseline"]["latencies_ms"] != final[0]["baseline"]["latencies_ms"]
    for task in final:
        assert set(task["contract"]["verdict_definitions"]) == {"PASS", "FAIL", "INCONCLUSIVE"}
        assert "Use null" in task["contract"]["output_field_semantics"]["latency_reduction_pct"]


@pytest.mark.parametrize("final_only,group_count", [(False, 10), (True, 6)])
def test_schedule_balances_positions_without_model_calls(final_only, group_count):
    pilot = load_tool("skill_eval_pilot")
    tasks = pilot.fixtures(final_only)
    schedule = pilot.build_schedule(tasks, final_only)
    assert schedule == pilot.build_schedule(tasks, final_only)
    matched = [entry for entry in schedule if entry["condition"] != "R"]
    assert len(matched) == group_count * 3
    conditions = {"N", "E-benchmark", "E-evidence"}
    groups = [matched[index:index + 3] for index in range(0, len(matched), 3)]
    keys = []
    for group in groups:
        assert Counter(entry["condition"] for entry in group) == Counter(conditions)
        assert len({(entry["task_id"], entry["repeat"]) for entry in group}) == 1
        keys.append((group[0]["task_id"], group[0]["repeat"]))
    assert len(set(keys)) == group_count
    repetitions = (
        {"holdout-3": 1, "holdout-4": 2, "holdout-5": 1, "holdout-6": 2}
        if final_only else
        {"dev-1": 1, "dev-2": 1, "holdout-3": 2, "holdout-4": 2,
         "holdout-5": 2, "holdout-6": 2}
    )
    expected = {
        (task_id, repeat)
        for task_id, count in repetitions.items()
        for repeat in range(1, count + 1)
    }
    assert set(keys) == expected
    for start in range(0, len(groups) - 2, 3):
        for position in range(3):
            assert Counter(group[position]["condition"] for group in groups[start:start + 3]) == (
                Counter(conditions)
            )
    for position in range(3):
        counts = Counter(group[position]["condition"] for group in groups)
        if final_only:
            assert counts == {condition: 2 for condition in conditions}
        else:
            assert max(counts.values()) - min(counts.values()) <= 1
    assert schedule[-3:] == [
        {"task_id": "R-" + mode, "condition": "R", "repeat": 1}
        for mode in ("relevant", "ambiguous", "irrelevant")
    ]


def test_retained_final_protocol_keeps_the_actual_unbalanced_order():
    protocol = json.loads(
        (ROOT / "evidence/skill-eval-v1-20261021/plan/protocol-v2.json").read_text("utf-8")
    )
    matched = [entry for entry in protocol["schedule"] if entry["condition"] != "R"]
    assert len(matched) == 18
    assert Counter(entry["condition"] for entry in matched[::3]) == {
        "N": 4, "E-benchmark": 2,
    }


def test_expired_real_executor_deadline_records_missing_events(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = run_trial(
        CodexClient("never-called", "xhigh", 1),
        {"id": "expired", "split": "development"},
        "N",
        1,
        workspace,
        tmp_path / "trial",
        tmp_path / "schema.json",
        tmp_path / "verifier.py",
        "Never executed",
        time.monotonic() - 1,
    )
    assert result["execution_state"] == "timeout"
    assert result["verdict"] == "INCONCLUSIVE"
    assert result["observations"]["usage"] is None


def test_host_trial_takes_verdict_rules_as_ports(tmp_path):
    from inferrsi.harness import skill_evaluation as host

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    seen = []

    def verdict(execution, trace, output, checks):
        seen.append((execution["status"], output, checks))
        return "PASS"

    arguments = (CodexClient("never-called", "xhigh", 1),
                 {"id": "expired", "split": "development"}, "N", 1, workspace)
    paths = (tmp_path / "schema.json", tmp_path / "verifier.py", "Never executed",
             time.monotonic() - 1)
    record = host.run_trial(
        *arguments, tmp_path / "trial", *paths,
        valid_checks=lambda checks: pytest.fail("No verifier result exists"),
        task_verdict=verdict)
    # The port is consulted once; an execution fact still overrides its answer.
    assert seen == [("timeout", None, None)]
    assert record["evaluation_error"] == "agent_timeout" and record["verdict"] == "INCONCLUSIVE"
    with pytest.raises(ValueError, match="Generation-only requires"):
        host.run_trial(*arguments, tmp_path / "second", *paths, valid_checks=bool,
                       task_verdict=verdict, generation_only=True)
    assert not (tmp_path / "second").exists()
    for name in ("valid_checks", "task_verdict", "summarize", "export_bundle"):
        assert not hasattr(host, name)


def test_harness_owned_writes_and_digests_match_infra_bytes(tmp_path):
    from inferrsi.harness import atomic_json, input_binding
    from inferrsi.infra import artifacts

    value = {"b": [1.5, None, True, 1e-07, 1e16], "a": "非 ASCII", "nested": {"z": 0.1 + 0.2}}
    atomic_json.atomic_write_json(tmp_path / "harness.json", value)
    artifacts.atomic_write_json(tmp_path / "infra.json", value)
    written = (tmp_path / "harness.json").read_bytes()
    assert written == (tmp_path / "infra.json").read_bytes()
    assert input_binding.file_sha256(tmp_path / "harness.json") == (
        artifacts.sha256(tmp_path / "infra.json"), len(written))
    assert sorted(path.name for path in tmp_path.iterdir()) == ["harness.json", "infra.json"]
    with pytest.raises(ValueError):
        atomic_json.atomic_write_json(tmp_path / "nan.json", {"value": float("nan")})
    assert not (tmp_path / "nan.json").exists()


def test_summary_normalizes_usage_before_the_pure_statistics():
    from inferrsi.benchmark import skill_evaluation as rules

    trial = {"task_id": "one", "repeat": 1, "condition": "N", "verdict": "PASS",
             "elapsed_seconds": 1, "observations": {"usage": {"input_tokens": -1,
                                                               "output_tokens": 3}}}
    assert summarize([trial])["conditions"]["N"]["input_tokens"] is None
    assert summarize([trial])["conditions"]["N"]["output_tokens"] == 3
    assert trial["observations"]["usage"]["input_tokens"] == -1
    normalized = {**trial, "observations": {"usage": {"input_tokens": None, "output_tokens": 3}}}
    assert rules.summarize([normalized]) == summarize([trial])
