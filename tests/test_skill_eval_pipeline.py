import copy
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from inferrsi.apps.skill_evaluation import run_trial, summarize
from inferrsi.infra.artifacts import atomic_write_json, sha256
from tools import skill_eval_pilot

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
DATA = json.loads((FIXTURES / "skill_eval_grading.json").read_text(encoding="utf-8"))


@dataclass
class FakeClient:
    mode: str
    answer: Path
    timeout_seconds: float = 20

    def command(self, workspace, final, schema, prompt):
        task = json.loads((workspace / "task.json").read_text(encoding="utf-8"))
        return (
            sys.executable, str(FIXTURES / "skill_eval_fake_client.py"),
            task.get("client_mode", self.mode), str(final), str(self.answer),
        )


def run_case(tmp_path, mode="correct", fault=None, case_id="equal-threshold", invalid_task=False):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    task = copy.deepcopy(DATA["task"])
    case = next(case for case in DATA["cases"] if case["id"] == case_id)
    for path, value in case["changes"].items():
        target = task
        *parents, key = path.split(".")
        for parent in parents:
            target = target[parent]
        target[key] = value
    if invalid_task:
        task["baseline"]["latencies_ms"] = [[0]]
    verifier = ROOT / "tools/skill_eval_verifier.py"
    if fault:
        task["verifier_fault"] = fault
        verifier = FIXTURES / "skill_eval_faulty_verifier.py"
    (workspace / "task.json").write_text(json.dumps(task), encoding="utf-8")
    answer = tmp_path / "expected-answer.json"
    answer.write_text(json.dumps(case["answer"]), encoding="utf-8")
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}', encoding="utf-8")
    record = run_trial(
        FakeClient(mode, answer, 1 if mode == "timeout" else 20),
        task, "N", 1, workspace, tmp_path / "trial", schema, verifier,
        "Offline fixture only", time.monotonic() + 45,
    )
    # Check persisted records, not only the return value or process exit code.
    assert json.loads((tmp_path / "trial/result.json").read_text(encoding="utf-8")) == record
    execution = json.loads((tmp_path / "trial/execution.json").read_text(encoding="utf-8"))
    assert execution["status"] == record["execution_state"]
    return record


@pytest.mark.parametrize("case_id", ["equal-threshold", "quality-failed", "kernel-only"])
def test_correct_answers_pass_even_when_input_report_does_not(tmp_path, case_id):
    record = run_case(tmp_path, case_id=case_id)
    assert record["verdict"] == "PASS"
    assert record["evaluation_error"] is None
    assert record["checks"]["passed"] is True
    assert record["inputs_unchanged"] is True


@pytest.mark.parametrize("mode", ["incorrect", "null", "nonobject"])
def test_completed_wrong_answers_are_task_failures(tmp_path, mode):
    record = run_case(tmp_path, mode)
    assert record["verdict"] == "FAIL"
    assert record["evaluation_error"] is None
    assert record["verification"]["returncode"] == 1
    assert record["checks"]["passed"] is False


@pytest.mark.parametrize(
    "mode,state,error",
    [
        ("timeout", "timeout", "agent_timeout"),
        ("nonzero", "failed", "agent_failed"),
        ("missing_output", "completed", "missing_output"),
        ("invalid_json", "completed", "invalid_output_json"),
        ("invalid_trace", "completed", "agent_trace_error"),
    ],
)
def test_client_failures_are_persisted_evaluation_errors(tmp_path, mode, state, error):
    record = run_case(tmp_path, mode)
    assert record["execution_state"] == state
    assert record["verdict"] == "INCONCLUSIVE"
    assert record["evaluation_error"] == error
    assert record["verification"] is None


@pytest.mark.parametrize(
    "fault,error",
    [("exception", "missing_verifier_result"), ("missing", "missing_verifier_result"),
     ("invalid_json", "invalid_verifier_result"), ("nonobject", "invalid_verifier_result"),
     ("missing_passed", "invalid_verifier_result"), ("string_passed", "invalid_verifier_result"),
     ("missing_assertions", "invalid_verifier_result"),
     ("empty_assertions", "invalid_verifier_result"),
     ("string_assertion", "invalid_verifier_result"),
     ("inconsistent", "invalid_verifier_result"), ("wrong_exit", "verifier_exit"),
     ("nan_assertion", "invalid_verifier_result"),
     ("infinite_assertion", "invalid_verifier_result"),
     ("overflow_assertion", "invalid_verifier_result")],
)
def test_verifier_faults_cannot_be_completed_passes(tmp_path, fault, error):
    record = run_case(tmp_path, fault=fault)
    assert record["execution_state"] == "completed"
    assert record["verdict"] == "INCONCLUSIVE"
    assert record["evaluation_error"] == error


def test_invalid_input_report_is_an_evaluation_error(tmp_path):
    record = run_case(tmp_path, invalid_task=True)
    assert record["verdict"] == "INCONCLUSIVE"
    assert record["evaluation_error"] == "missing_verifier_result"


def test_pilot_persists_mixed_summary_without_model_calls(tmp_path, monkeypatch):
    for name in ("plan", "validator", "results", "patch"):
        (tmp_path / name).mkdir()
    tasks = []
    for mode in ("correct", "incorrect", "missing_output", "unknown_usage", "partial_usage"):
        tasks.append({**copy.deepcopy(DATA["task"]), "id": mode, "client_mode": mode})
    atomic_write_json(tmp_path / "plan/tasks.json", tasks)
    atomic_write_json(tmp_path / "plan/schema.json", skill_eval_pilot.SCHEMA)
    shutil.copyfile(ROOT / "tools/skill_eval_verifier.py", tmp_path / "validator/verify.py")
    plan = {
        "model": "offline-fake", "effort": "test", "timeout_seconds": 20,
        "campaign_seconds": 120, "token_stop_new_trials": 10000,
        "tasks_sha256": sha256(tmp_path / "plan/tasks.json"),
        "schema_sha256": sha256(tmp_path / "plan/schema.json"),
        "verifier_sha256": sha256(tmp_path / "validator/verify.py"),
        "frozen_files": {}, "skills": {},
        "schedule": [{"task_id": task["id"], "condition": "N", "repeat": 1} for task in tasks],
    }
    atomic_write_json(tmp_path / "plan/freeze.json", plan)
    answer = tmp_path / "expected-answer.json"
    atomic_write_json(answer, DATA["cases"][0]["answer"])
    monkeypatch.setattr(
        skill_eval_pilot, "CodexClient",
        lambda model, effort, timeout_seconds: FakeClient("correct", answer, timeout_seconds),
    )
    skill_eval_pilot.execute(tmp_path)
    summary_path = tmp_path / "results/summary.json"
    group = json.loads(summary_path.read_text(encoding="utf-8"))["conditions"]["N"]
    assert group["trials"] == 5
    assert group["verdicts"] == {"PASS": 3, "FAIL": 1, "INCONCLUSIVE": 1}
    assert group["evaluation_errors"] == 1
    assert group["input_tokens"] is None and group["output_tokens"] is None
    assert group["cost"] is None
    trials = json.loads((tmp_path / "results/trials.json").read_text(encoding="utf-8"))
    assert [trial["verdict"] for trial in trials] == [
        "PASS", "FAIL", "INCONCLUSIVE", "PASS", "PASS",
    ]
    for trial in trials:
        persisted = tmp_path / "results" / (trial["task_id"] + "-N-1") / "result.json"
        assert json.loads(persisted.read_text(encoding="utf-8")) == trial
    status_path = tmp_path / "results/status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["observed_tokens"] == 77
    assert status["token_usage_complete"] is False
    # Recovered completed records must retain partial usage without rerunning the client.
    monkeypatch.setattr(FakeClient, "command", lambda *args: pytest.fail("Unexpected rerun"))
    skill_eval_pilot.execute(tmp_path)
    assert json.loads(status_path.read_text(encoding="utf-8"))["observed_tokens"] == 77


def test_partial_usage_stays_unknown_per_metric():
    trial = {
        "task_id": "partial", "condition": "N", "repeat": 1, "verdict": "PASS",
        "elapsed_seconds": 1, "observations": {"usage": {"input_tokens": 17}},
    }
    group = summarize([trial])["conditions"]["N"]
    assert group["input_tokens"] == 17
    assert group["output_tokens"] is None
