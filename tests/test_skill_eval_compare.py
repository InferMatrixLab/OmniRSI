import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from inferrsi.infra.artifacts import sha256, write_json
from inferrsi.knowledge_operations import KnowledgeReader, RevisionConflict
from tests.fixtures.skill_eval_compare_support import (
    DATA,
    ROOT,
    FixedOutputClient,
    git,
)
from tests.fixtures.skill_eval_compare_support import (
    configuration as comparison_configuration,
)
from tools import skill_eval_compare as compare
from tools import skill_eval_pilot
from tools import skill_eval_verifier as verifier

SNAPSHOT_GIT_VERSION = subprocess.check_output(("git", "--version"), text=True)


def configuration(*args, **kwargs):
    try:
        compare.require_git_version(SNAPSHOT_GIT_VERSION)
    except ValueError as error:
        pytest.skip(str(error))
    return comparison_configuration(*args, **kwargs)


@pytest.mark.parametrize("mode", ["addition", "update"])
def test_real_snapshots_isolated_inputs_and_pair_outcomes(tmp_path, mode):
    config, repo = configuration(tmp_path, mode, ids=("fix", "regression", "same", "wrong",
                                                    "error", "discovery"), repeats=1)
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    plan = compare.load_plan(output)
    assert plan["same_bundle"] is False
    assert any("exam.txt" in name for name in plan["source_changes"])
    candidate = output / "plan/bundles/candidate"
    assert (candidate / "selected/SKILL.md").read_text(encoding="utf-8").endswith("New body\n")
    assert (candidate / "selected/resource.txt").read_bytes() == b"new resource"
    assert (candidate / "shared/notes.md").read_bytes() == b"new dependency"
    if mode == "addition":
        assert plan["arms"]["baseline"]["bundle"] is None
        assert not (output / "plan/bundles/baseline").exists()
    else:
        baseline = output / "plan/bundles/baseline"
        assert (baseline / "selected/SKILL.md").read_text(encoding="utf-8").endswith("Old body\n")
        assert (baseline / "selected/resource.txt").read_bytes() == b"old resource"
        assert (baseline / "shared/notes.md").read_bytes() == b"old dependency"
        assert plan["arms"]["baseline"]["bundle"]["dependencies"] != (
            plan["arms"]["candidate"]["bundle"]["dependencies"])
    modes = {("fix", "baseline"): "incorrect", ("regression", "candidate"): "incorrect",
             ("wrong", "baseline"): "incorrect", ("wrong", "candidate"): "incorrect",
             ("error", "candidate"): "invalid_json"}
    client = FixedOutputClient(tmp_path, modes)
    result = compare.execute(output, authorization, client=client)
    assert result["counts"] == {"fix": 1, "regression": 1, "unchanged_correct": 1,
                                "unchanged_incorrect": 1, "missing_pair": 0, "inconclusive": 1}
    assert len(result["discovery"]) == 2
    assert result["conditions"]["candidate"]["expected_trials"] == 5
    assert result["discovery_conditions"]["candidate"]["expected_trials"] == 1
    assert len(client.calls) == 12
    assert len({call["workspace"] for call in client.calls}) == 12
    assert all(not call["workspace"].exists() for call in client.calls)
    assert [call["role"] for call in client.calls[:4]] == [
        "baseline", "candidate", "candidate", "baseline"]
    for index in range(0, len(client.calls), 2):
        left, right = client.calls[index:index + 2]
        assert left["inputs"]["task.json"] == right["inputs"]["task.json"]
        assert left["schema_sha256"] == right["schema_sha256"]
    for call in client.calls:
        assert not any("expected-answer" in name or ".git" in name for name in call["inputs"])
        if mode == "addition" and call["role"] == "baseline":
            assert set(call["inputs"]) == {"task.json"}
            assert "selected" not in call["prompt"]
        if call["case_id"] == "discovery":
            assert "Use the assigned skill" not in call["prompt"]
            if call["role"] == "candidate":
                assert ".agents/skills/selected/SKILL.md" in call["inputs"]
    # The wrong candidate is independently judged by the retained audit verifier.
    regression = next(p for p in result["pairs"] if p["case_id"] == "regression")
    assert regression["candidate"]["checks"]["passed"] is False
    assert any(value is False for value in regression["candidate"]["checks"]["assertions"].values())
    assert result["conditions"]["candidate"]["evaluation_errors"] == 1
    saved = (output / "report.json").read_bytes(), (output / "report.md").read_bytes()
    calls = len(client.calls)
    repo.rename(tmp_path / "source-unavailable")
    compare.report(output)
    assert len(client.calls) == calls
    assert saved == ((output / "report.json").read_bytes(), (output / "report.md").read_bytes())
    assert str(tmp_path) not in (output / "report.json").read_text(encoding="utf-8")
    for pair in result["pairs"]:
        for role in compare.ROLES:
            for name in pair[role]["evidence"].values():
                assert (output / name).is_file()
    with pytest.raises(ValueError, match="fresh results"):
        compare.execute(output, authorization, client=client)


def test_budget_stops_leave_missing_sides_and_unknown_usage(tmp_path):
    config, repo = configuration(tmp_path, ids=("one",), repeats=2, token_stop_new_trials=1)
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    client = FixedOutputClient(tmp_path, {("one", "baseline"): "partial_usage"})
    result = compare.execute(output, authorization, client=client)
    assert len(client.calls) == 1
    assert result["status"]["stop_reason"] == "token_budget"
    assert result["status"]["observed_tokens"] == 17
    assert result["counts"]["missing_pair"] == 2
    assert result["counts"]["fix"] == result["counts"]["regression"] == 0
    assert result["conditions"]["baseline"]["input_tokens"] is None
    assert result["conditions"]["baseline"]["output_tokens"] is None
    assert result["conditions"]["candidate"]["cost"] is None
    assert result["pairs"][0]["baseline"]["output"] is not None
    assert result["pairs"][0]["candidate"]["missing"] is True
    assert result["pairs"][1]["baseline"]["missing"] is True


@pytest.mark.parametrize("usage", ["malformed", [1]])
def test_legacy_malformed_usage_report_stays_unknown_without_rewriting_evidence(tmp_path, usage):
    config, repo = configuration(tmp_path, ids=("one",))
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    client = FixedOutputClient(tmp_path)
    original = compare.execute(output, authorization, client=client)
    path = output / original["pairs"][0]["candidate"]["evidence"]["result.json"]
    record = compare.read_json(path)
    # Model a legacy controller that retained raw provider usage with a valid receipt.
    record["observations"]["usage"] = usage
    write_json(path, record)
    receipt = path.parent / "receipt.json"
    write_json(receipt, {"result_sha256": sha256(path)})
    saved = path.read_bytes(), receipt.read_bytes()
    result = compare.report(output)
    assert result["pairs"][0]["candidate"]["observations"]["usage"] is None
    assert result["conditions"]["candidate"]["input_tokens"] is None
    assert result["conditions"]["candidate"]["output_tokens"] is None
    assert len(client.calls) == 2
    assert saved == (path.read_bytes(), receipt.read_bytes())


def test_run_status_receipt_rejects_changed_completion_and_budget_fields(tmp_path):
    config, repo = configuration(tmp_path, ids=("one",), token_stop_new_trials=1)
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    client = FixedOutputClient(tmp_path, {("one", "baseline"): "partial_usage"})
    original = compare.execute(output, authorization, client=client)
    assert original["status"]["execution_state"] == "stopped"
    assert original["status"]["stop_reason"] == "token_budget"
    assert original["status"]["client_runtime"]["version"] is None
    path, receipt = output / "results/status.json", output / "results/status.receipt.json"
    record, proof = path.read_bytes(), receipt.read_bytes()
    saved = (output / "report.json").read_bytes(), (output / "report.md").read_bytes()
    edits = {"execution_state": "completed", "recorded_trials": 2, "expected_trials": 99,
             "stop_reason": None, "observed_tokens": 0,
             "client_runtime": {"version": "fabricated"}}
    mutations = [json.dumps({**json.loads(record), key: value}).encode()
                 for key, value in edits.items()]
    mutations += [json.dumps(edits).encode(), b"{broken", b"null"]
    for mutation in mutations:
        path.write_bytes(mutation)
        result = compare.report(output)
        status = result["status"]
        assert status["execution_state"] == "unverified"
        assert status["evaluation_error"] == "retained_status_record_hash_mismatch"
        assert status["expected_trials"] == 2
        assert all(status[key] is None for key in (
            "recorded_trials", "observed_tokens", "stop_reason", "client_runtime"))
        assert result["pairs"] == original["pairs"]
        assert path.read_bytes() == mutation and receipt.read_bytes() == proof
        assert compare.report(output) == result
        path.write_bytes(record)
        assert compare.report(output) == original
        assert saved == ((output / "report.json").read_bytes(), (output / "report.md").read_bytes())
    for mutation, error in ((None, "missing_retained_status_receipt"),
                            (b"{broken", "invalid_retained_status_receipt")):
        if mutation is None:
            receipt.unlink()
        else:
            receipt.write_bytes(mutation)
        assert compare.report(output)["status"]["evaluation_error"] == error
        assert path.read_bytes() == record
        receipt.write_bytes(proof)
        assert compare.report(output) == original
    path.unlink()
    status = compare.report(output)["status"]
    assert status["execution_state"] == "unverified"
    assert status["evaluation_error"] == "missing_retained_status_record"
    assert status["recorded_trials"] is None and status["observed_tokens"] is None
    path.write_bytes(record)
    assert compare.report(output) == original
    assert len(client.calls) == 1


def test_real_client_runtime_resolution_only_probes_version_without_models(tmp_path):
    client = compare.CodexClient("never-called", "test", 60, sys.executable)
    resolved, runtime = compare.resolve_client(client)
    assert resolved.executable == str(Path(sys.executable).resolve())
    assert runtime["executable"] == os.path.basename(resolved.executable)
    assert runtime["executable_sha256"] == sha256(Path(resolved.executable))
    assert sys.version.split()[0] in runtime["version"]
    assert runtime["implementation"].endswith(".CodexClient")
    # Isolate relative PATH lookup in a real child process without changing the test host.
    script = tmp_path / "runtime.py"
    script.write_text(
        "import json, sys\n"
        "from tools.skill_eval_compare import CodexClient, resolve_client\n"
        "client, runtime = resolve_client(CodexClient('never-called', 'test', 60, sys.argv[1]))\n"
        "print(json.dumps({'executable': client.executable, 'runtime': runtime}))\n",
        encoding="utf-8",
    )
    environment = {**os.environ, "PATH": ".",
                   "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "src")))}
    observed = json.loads(subprocess.check_output(
        (sys.executable, str(script), os.path.basename(sys.executable)),
        cwd=Path(sys.executable).parent, env=environment, text=True,
    ))
    assert observed["executable"] == resolved.executable
    assert observed["runtime"] == runtime


def test_executable_snapshot_entries_are_rejected_instead_of_losing_modes(tmp_path):
    config, repo = configuration(tmp_path, ids=("one",))
    raw = json.loads(config.read_text())
    candidate = raw["snapshots"]["candidate"]["git_revision"]
    resource = "library/selected/resource.txt"
    git(repo, "update-index", "--chmod=+x", resource)
    git(repo, "commit", "--quiet", "-m", "Fixture executable resource")
    executable = git(repo, "rev-parse", "HEAD")
    assert git(repo, "rev-parse", executable + ":" + resource) == (
        git(repo, "rev-parse", candidate + ":" + resource))
    for role in compare.ROLES:
        changed = {**raw, "snapshots": {**raw["snapshots"], role: {"git_revision": executable}}}
        write_json(config, changed)
        output = tmp_path / role
        with pytest.raises(ValueError, match="Executable snapshot entries are not supported"):
            compare.prepare(config, repo, output)
        assert list((output / "results").iterdir()) == []


@pytest.mark.parametrize("failure,error", [("timeout", "agent_timeout"),
    ("nonzero", "agent_failed"), ("missing_output", "missing_output"),
    ("invalid_json", "invalid_output_json"), ("invalid_utf8", "invalid_output_json"),
    ("markdown_fence", "invalid_output_json"),
    ("invalid_trace", "agent_trace_error")])
def test_client_errors_cannot_be_fixes_or_regressions(tmp_path, failure, error):
    config, repo = configuration(tmp_path, ids=("one",))
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    client = FixedOutputClient(tmp_path, {("one", "candidate"): failure})
    result = compare.execute(output, authorization, client=client)
    trial = result["pairs"][0]["candidate"]
    assert trial["verdict"] == "INCONCLUSIVE" and trial["evaluation_error"] == error
    assert result["counts"]["inconclusive"] == 1
    assert result["counts"]["fix"] == result["counts"]["regression"] == 0
    assert result["conditions"]["candidate"]["evaluation_errors"] == 1
    persisted = json.loads((output / "results/one-candidate-1/result.json").read_text())
    assert persisted["evaluation_error"] == error
    status = json.loads((output / "results/status.json").read_text())
    assert status["recorded_trials"] == status["expected_trials"] == len(client.calls) == 2
    if failure in ("timeout", "nonzero"):
        assert status["execution_state"] == "stopped"
        assert status["stop_reason"] == (
            "client_timeout" if failure == "timeout" else "client_failed")
    else:
        assert status["execution_state"] == "completed" and status["stop_reason"] is None
    regenerated = compare.report(output)
    for name in ("execution_state", "stop_reason", "recorded_trials", "expected_trials"):
        assert regenerated["status"][name] == result["status"][name] == status[name]
    if failure == "invalid_utf8":
        assert trial["output"] == "\\xff\\xfe"
        assert compare.report(output)["pairs"][0]["candidate"]["output"] == "\\xff\\xfe"
        assert (output / "results/one-candidate-1/answer.json").read_bytes() == b"\xff\xfe"
    if failure == "markdown_fence":
        rendered = (output / "report.md").read_text(encoding="utf-8")
        assert "\n`````````text\n" + trial["output"] + "\n`````````\n" in rendered
        assert "\n````text\n" + trial["output"] not in rendered


@pytest.mark.parametrize("fault", ["exception", "wrong_exit", "invalid_json"])
def test_grader_errors_and_interrupted_attempt_are_retained(tmp_path, fault):
    config, repo = configuration(tmp_path, ids=("one",))
    raw = json.loads(config.read_text())
    # Freeze the test fault in the grader rather than model-visible case data.
    grader = tmp_path / "faulty.py"
    source = (ROOT / "tests/fixtures/skill_eval_faulty_verifier.py").read_text(encoding="utf-8")
    grader.write_text(source.replace('task["verifier_fault"]', repr(fault)), encoding="utf-8")
    raw["grader"] = grader.name
    write_json(config, raw)
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    result = compare.execute(output, authorization, client=FixedOutputClient(tmp_path))
    assert result["counts"]["inconclusive"] == 1
    assert result["conditions"]["baseline"]["evaluation_errors"] == 1
    trial_path = output / "results/one-candidate-1/result.json"
    trial_path.unlink()
    result = compare.report(output)
    assert result["pairs"][0]["candidate"]["evaluation_error"] == "interrupted_attempt"
    assert result["counts"]["inconclusive"] == 1
    assert result["conditions"]["candidate"]["evaluation_errors"] == 1
    assert not trial_path.exists()


def test_snapshot_revision_and_modified_frozen_resources_are_rejected(tmp_path):
    config, repo = configuration(tmp_path, ids=("one",))
    raw = json.loads(config.read_text())
    raw["snapshots"]["candidate"]["bundle_revision"] = "sha256:" + "0" * 64
    write_json(config, raw)
    with pytest.raises(RevisionConflict):
        compare.prepare(config, repo, tmp_path / "mismatch")
    raw["snapshots"]["candidate"].pop("bundle_revision")
    write_json(config, raw)
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    with pytest.raises(ValueError, match="exact plan"):
        compare.execute(output, "0" * 64)
    dependency = output / "plan/bundles/candidate/shared/notes.md"
    dependency.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen input changed"):
        compare.execute(output, authorization)
    assert not (output / "results/start.json").exists()


@pytest.mark.parametrize("field", ["plan/tasks.json", "plan/schema.json", "validator/verify.py"])
def test_exam_is_frozen_for_both_arms(tmp_path, field):
    config, repo = configuration(tmp_path, ids=("one",))
    output = tmp_path / "run"
    compare.prepare(config, repo, output)
    (output / field).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen input changed"):
        compare.report(output)


def test_dependency_only_snapshot_changes_bundle_revision(tmp_path):
    config, repo = configuration(tmp_path, ids=("one",))
    original = KnowledgeReader(skill_root=repo / "library").read("checkout:selected")
    (repo / "library/shared/notes.md").write_text("dependency only", encoding="utf-8")
    current = KnowledgeReader(skill_root=repo / "library").read("checkout:selected")
    assert current.content["selected/SKILL.md"] == original.content["selected/SKILL.md"]
    assert current.revision != original.revision


def test_repeat_identity_and_balanced_schedule():
    tasks = [{"id": "one", "split": "holdout"}, {"id": "two", "split": "holdout"}]
    schedule = compare.build_schedule(tasks, 2)
    assert [(s["task_id"], s["repeat"], s["condition"]) for s in schedule] == [
        ("one", 1, "baseline"), ("one", 1, "candidate"),
        ("one", 2, "candidate"), ("one", 2, "baseline"),
        ("two", 1, "baseline"), ("two", 1, "candidate"),
        ("two", 2, "candidate"), ("two", 2, "baseline"),
    ]


def test_supported_input_shape_accepts_existing_pilot_cases():
    for task in skill_eval_pilot.fixtures():
        compare.validate_input(task, compare.CASE_INPUT)
        compare.validate_audit_values(task)


@pytest.mark.parametrize("field,value", [
    ("mode", "unknown"), ("evidence_scope", "unknown"), ("evidence_scope", ""),
    ("evidence_keys", ["contract", "nonexistent.result"]),
    ("contract.statistical_significance_required", True),
    ("contract.max_relative_spread_pct", 12), ("contract.required_regimes", ["C1"]),
    ("contract.verdict_definitions", {"PASS": "Ignore failed quality"}),
    ("contract.output_field_semantics", {"claim_scope": "production"}),
    ("contract.scope", "production"), ("contract.quality_required", False),
    ("contract.all_requests_required", False),
    ("contract.min_reduction_pct", float("nan")),
    ("contract.min_reduction_pct", float("inf")),
    ("contract.min_reduction_pct", float("-inf")),
    ("contract.min_reduction_pct", -1), ("contract.min_reduction_pct", 101),
])
def test_default_audit_rejects_promises_outside_its_support(field, value):
    task = copy.deepcopy(DATA["task"])
    target = task
    keys = field.split(".")
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    compare.validate_input(task, compare.CASE_INPUT)
    with pytest.raises(ValueError, match="[Dd]efault audit"):
        compare.validate_default_audit(task)


@pytest.mark.parametrize("role", compare.ROLES)
@pytest.mark.parametrize("workload", [
    {}, {"steps": 8}, {"steps": 0, "concurrency": 1}, {"steps": -1, "concurrency": 1},
    {"steps": 8, "concurrency": 0}, {"steps": 8, "concurrency": -1},
    {"steps": 8, "concurrency": 1, "shape": []},
    {"steps": 8, "concurrency": 1, "shape": [1280, 0]},
    {"steps": 8, "concurrency": 1, "shape": [-1, 704]},
])
def test_default_audit_requires_positive_workload_dimensions(role, workload):
    task = copy.deepcopy(DATA["task"])
    task[role]["workload"] = workload
    with pytest.raises(ValueError, match="positive workload"):
        compare.validate_default_audit(task)


def test_default_audit_requires_complete_inputs():
    original = DATA["task"]
    for name in ("mode", "contract", "evidence_scope", *compare.ROLES, "evidence_keys"):
        task = copy.deepcopy(original)
        task.pop(name)
        with pytest.raises(ValueError, match="Default audit requires"):
            compare.validate_default_audit(task)
    for role in compare.ROLES:
        for name in compare.REPORT_INPUT:
            task = copy.deepcopy(original)
            task[role].pop(name)
            with pytest.raises(ValueError, match="complete report"):
                compare.validate_default_audit(task)
    task = copy.deepcopy(original)
    task["contract"] = {}
    with pytest.raises(ValueError, match="requires contract.min_reduction_pct"):
        compare.validate_default_audit(task)
    for name in compare.DEFAULT_AUDIT_EVIDENCE:
        task = copy.deepcopy(original)
        task["evidence_keys"].remove(name)
        with pytest.raises(ValueError, match="Missing default audit evidence_keys"):
            compare.validate_default_audit(task)


@pytest.mark.parametrize("changes", [
    {"quality": "failed"}, {"quality": "missing"}, {"failed_requests": 1},
    {"failed_requests": 1, "reported_requests": 5, "latencies_ms": [[100] * 5]},
])
def test_default_audit_rejects_unsupported_baseline_evidence(changes):
    task = copy.deepcopy(DATA["task"])
    task["baseline"].update(changes)
    compare.validate_input(task, compare.CASE_INPUT)
    compare.validate_audit_values(task)
    with pytest.raises(ValueError, match="passed baseline with no failed requests"):
        compare.validate_default_audit(task)


def test_default_audit_supported_gates_match_the_existing_grader():
    for case in DATA["cases"]:
        task = copy.deepcopy(DATA["task"])
        task["contract"].update(scope="fixed_workload", quality_required=True,
                                all_requests_required=True, statistical_significance_required=False)
        for field, value in case.get("changes", {}).items():
            target = task
            keys = field.split(".")
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
        if task["mode"] == "extract":
            task["contract"] = {}
        # Preserve the legacy verifier's branch expectations with complete comparison inputs.
        if case["id"] in ("below-threshold", "above-threshold"):
            task["candidate"]["latencies_ms"] = [group * 2
                                                  for group in task["candidate"]["latencies_ms"]]
        if case["id"] == "omitted-failures":
            task["candidate"].update(total_requests=7, reported_requests=6)
        compare.validate_input(task, compare.CASE_INPUT)
        compare.validate_audit_values(task)
        compare.validate_default_audit(task)
        assert verifier.verify(task, case["answer"])["passed"] is True
    task = copy.deepcopy(DATA["task"])
    task.update(mode="extract", contract={})
    answer = {**DATA["cases"][0]["answer"], "report_verdict": None, "claim_scope": "none"}
    compare.validate_default_audit(task)
    assert verifier.verify(task, answer)["passed"] is True
    task["contract"]["min_reduction_pct"] = 20
    with pytest.raises(ValueError, match="extract mode cannot enforce"):
        compare.validate_default_audit(task)


@pytest.mark.parametrize("role", compare.ROLES)
@pytest.mark.parametrize("latencies", [[[1]], [[100] * 5], [[100] * 7]])
def test_default_audit_requires_one_latency_sample_per_reported_request(role, latencies):
    task = copy.deepcopy(DATA["task"])
    task[role]["latencies_ms"] = latencies
    compare.validate_input(task, compare.CASE_INPUT)
    compare.validate_audit_values(task)
    with pytest.raises(ValueError, match="latency samples must match reported_requests"):
        compare.validate_default_audit(task)


def test_default_audit_boundary_runs_before_output_and_covers_explicit_grader_copies(tmp_path):
    config, repo = configuration(tmp_path, ids=("one",))
    original = json.loads((tmp_path / "cases.json").read_text())
    raw = json.loads(config.read_text())
    copied = tmp_path / "copied_grader.py"
    shutil.copyfile(ROOT / "tools/skill_eval_verifier.py", copied)
    for index, grader in enumerate((None, str(ROOT / "tools/skill_eval_verifier.py"), copied.name)):
        if grader is not None:
            raw["grader"] = grader
        write_json(config, raw)
        for field, value in (("contract.statistical_significance_required", True),
                             ("contract.min_reduction_pct", float("nan")), ("evidence_keys", []),
                             ("baseline.quality", "failed"), ("baseline.failed_requests", 1),
                             ("baseline.latencies_ms", [[1]]), ("candidate.latencies_ms", [[1]])):
            tasks = copy.deepcopy(original)
            target = tasks[0]
            keys = field.split(".")
            for key in keys[:-1]:
                target = target[key]
            target[keys[-1]] = value
            (tmp_path / "cases.json").write_text(json.dumps(tasks), encoding="utf-8")
            output = tmp_path / f"invalid-{index}-{field}"
            with pytest.raises(ValueError, match="[Dd]efault audit"):
                compare.prepare(config, repo, output)
            assert not output.exists()
    tasks = copy.deepcopy(original)
    tasks[0]["contract"]["statistical_significance_required"] = True
    write_json(tmp_path / "cases.json", tasks)
    raw["grader"] = "custom_grader.py"
    (tmp_path / raw["grader"]).write_text("raise RuntimeError('Custom grader, never executed')\n")
    write_json(config, raw)
    # A separate frozen grader owns its contract; preparation does not invoke it.
    compare.prepare(config, repo, tmp_path / "custom")


@pytest.mark.parametrize("role", compare.ROLES)
def test_invalid_audit_values_are_rejected_before_freezing(tmp_path, role):
    config, repo = configuration(tmp_path, ids=("one",))
    cases = tmp_path / "cases.json"
    original = json.loads(cases.read_text())
    invalid = [("quality", value, "Unsupported quality") for value in ("unknown", "PASSED", "")]
    invalid += [("latencies_ms", value, "finite positive latencies") for value in (
        [[-1]], [[0]], [[100, -1]], [[float("nan")]], [[float("inf")]],
        [[float("-inf")]], [], [[]], [[100], []])]
    for index, (key, value, error) in enumerate(invalid):
        tasks = json.loads(json.dumps(original))
        if role == "candidate":
            tasks[0]["baseline"]["latencies_ms"] = [[100]]
        tasks[0][role][key] = value
        # Exercise external JSON parsing, including nonfinite numbers accepted by json.loads.
        cases.write_text(json.dumps(tasks), encoding="utf-8")
        output = tmp_path / f"invalid-{index}"
        with pytest.raises(ValueError, match=error):
            compare.prepare(config, repo, output)
        assert not output.exists()
    for quality in ("passed", "failed", "missing"):
        task = json.loads(json.dumps(original[0]))
        task[role]["quality"] = quality
        compare.validate_audit_values(task)


@pytest.mark.parametrize("missing_kind", ["commit", "tree", "blob", "diff_tree"])
def test_missing_promisor_objects_fail_locally_without_lazy_fetch(tmp_path, missing_kind):
    config, origin = configuration(tmp_path, ids=("one",))
    raw = json.loads(config.read_text())
    if missing_kind == "diff_tree":
        folder = origin / "outside"
        folder.mkdir()
        (folder / "note.txt").write_text("Unselected source tree", encoding="utf-8")
        git(origin, "add", "outside")
        git(origin, "commit", "--quiet", "-m", "Fixture unselected tree")
        raw["snapshots"]["candidate"]["git_revision"] = git(origin, "rev-parse", "HEAD")
        write_json(config, raw)
    before = raw["snapshots"]["baseline"]["git_revision"]
    after = raw["snapshots"]["candidate"]["git_revision"]
    missing = {"commit": before, "tree": before + ":library",
               "blob": before + ":library/selected/resource.txt", "diff_tree": after + ":outside"}
    digest = git(origin, "rev-parse", missing[missing_kind])
    git(origin, "config", "uploadpack.allowFilter", "true")
    git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    repo = tmp_path / "partial"
    git(origin, "clone", "--quiet", "--no-hardlinks", "--no-checkout", str(origin), str(repo))
    git(repo, "config", "extensions.partialClone", "origin")
    git(repo, "config", "remote.origin.promisor", "true")
    git(repo, "config", "remote.origin.partialclonefilter", "blob:none")
    object_path = repo / ".git/objects" / digest[:2] / digest[2:]
    assert object_path.is_file()
    object_path.unlink()
    trace = tmp_path / "offline-trace.jsonl"
    environment = {**os.environ, "GIT_NO_LAZY_FETCH": "0", "GIT_TRACE2_EVENT": str(trace),
                   "PYTHONPATH": os.pathsep.join((str(ROOT), str(ROOT / "src")))}
    output = tmp_path / "run"
    process = subprocess.run(
        (sys.executable, "-m", "tools.skill_eval_compare", "prepare", "--config", str(config),
         "--repo", str(repo), "--output", str(output)), cwd=ROOT, env=environment,
        capture_output=True, text=True,
    )
    assert process.returncode != 0
    assert not (output / "plan/freeze.json").exists()
    assert not (output / "results/start.json").exists()
    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert not any(event["event"] == "child_start" and "fetch" in event.get("argv", [])
                   for event in events)
    # The real local promisor can supply the object when lazy fetching is explicitly enabled.
    control_trace = tmp_path / "fetch-control.jsonl"
    control = subprocess.run(("git", "cat-file", "-p", digest), cwd=repo,
                             env={**environment, "GIT_TRACE2_EVENT": str(control_trace)},
                             capture_output=True)
    assert control.returncode == 0
    events = [json.loads(line) for line in control_trace.read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "child_start" and "fetch" in event.get("argv", [])
               for event in events)


def test_git_version_preflight_rejects_unsupported_clients():
    for version in ("git version 2.43.0", "git version 2.44.5", "git version 1.9.0", "unknown"):
        with pytest.raises(ValueError, match="requires Git >= 2.45"):
            compare.require_git_version(version)
    for version in ("git version 2.45.0", "git version 2.45.0.rc0",
                    "git version 2.53.0.windows.2", "git version 3.0.0"):
        compare.require_git_version(version)


@pytest.mark.parametrize("role", compare.ROLES)
def test_request_counts_reject_unaccounted_or_inconsistent_evidence(tmp_path, role):
    config, repo = configuration(tmp_path, ids=("one",))
    cases = tmp_path / "cases.json"
    original = json.loads(cases.read_text())
    invalid = [(-1, 0, 0), (6, -1, 0), (6, 6, -1), (6, 7, 0), (6, 6, 7),
               (6, 5, 0), (6, 4, 1), (0, 0, 0)]
    for index, (total, reported, failed) in enumerate(invalid):
        tasks = json.loads(json.dumps(original))
        tasks[0][role].update(total_requests=total, reported_requests=reported,
                             failed_requests=failed)
        write_json(cases, tasks)
        output = tmp_path / f"counts-{index}"
        with pytest.raises(ValueError, match="request"):
            compare.prepare(config, repo, output)
        assert not output.exists()
    for name in ("total_requests", "reported_requests", "failed_requests"):
        tasks = json.loads(json.dumps(original))
        tasks[0][role].pop(name)
        write_json(cases, tasks)
        output = tmp_path / name
        with pytest.raises(ValueError, match="Incomplete request count fields"):
            compare.prepare(config, repo, output)
        assert not output.exists()
    for reported, failed in ((6, 0), (6, 1), (5, 1), (3, 3)):
        task = json.loads(json.dumps(original[0]))
        task[role].update(total_requests=6, reported_requests=reported, failed_requests=failed)
        compare.validate_audit_values(task)


def test_prepare_and_report_cli_are_model_free(tmp_path):
    config, repo = configuration(tmp_path, "addition", ids=("one",))
    output = tmp_path / "run"
    prefix = [sys.executable, "-m", "tools.skill_eval_compare"]
    # Match pytest's source path in child processes without requiring an editable install.
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    subprocess.run([*prefix, "prepare", "--config", str(config), "--repo", str(repo),
                    "--output", str(output)], cwd=ROOT, env=environment,
                   check=True, capture_output=True)
    subprocess.run([*prefix, "report", "--output", str(output)], cwd=ROOT,
                   env=environment, check=True, capture_output=True)
    assert json.loads((output / "report.json").read_text())["counts"]["missing_pair"] == 1
    assert not (output / "results/start.json").exists()
    denied = subprocess.run([*prefix, "execute", "--output", str(output)], cwd=ROOT,
                            env=environment, capture_output=True)
    assert denied.returncode != 0 and b"--authorize" in denied.stderr


def test_invalid_configs_fail_before_creating_outputs(tmp_path):
    config, repo = configuration(tmp_path, ids=("one",))
    raw = json.loads(config.read_text())
    raw["limits"]["max_task_calls"] = 1
    write_json(config, raw)
    with pytest.raises(ValueError, match="max_task_calls"):
        compare.prepare(config, repo, tmp_path / "too-many")
    assert not (tmp_path / "too-many").exists()
    raw["limits"]["max_task_calls"] = 2
    raw["snapshots"]["candidate"]["git_revision"] = "main"
    write_json(config, raw)
    with pytest.raises(ValueError, match="immutable"):
        compare.prepare(config, repo, tmp_path / "mutable")
    assert not (tmp_path / "mutable").exists()


def test_required_execution_case_fields_are_validated_before_freeze(tmp_path):
    config, repo = configuration(tmp_path, ids=("one",))
    cases = tmp_path / "cases.json"
    original = json.loads(cases.read_text())
    invalid = []
    for key in ("request", "id", "split"):
        task = {name: value for name, value in original[0].items() if name != key}
        invalid.append((task, "Missing required case fields"))
    invalid.append(({"id": "one", "split": "holdout"}, "Missing required case fields"))
    for value in (None, 42, ["audit"]):
        invalid.append(({**original[0], "request": value}, "Unsupported input type"))
    for index, (task, error) in enumerate(invalid):
        write_json(cases, [task])
        output = tmp_path / f"invalid-{index}"
        with pytest.raises(ValueError, match=error):
            compare.prepare(config, repo, output)
        assert not output.exists()


def test_edited_freeze_semantics_cannot_pass_with_a_recomputed_hash(tmp_path):
    # The authorization hash is taken over freeze.json, so anyone who edits it can
    # recompute that hash. The fields it states must agree with the frozen inputs.
    config, repo = configuration(tmp_path, ids=("one", "two"), repeats=1)
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    plan = compare.load_plan(output)
    assert len(plan["schedule"]) == 4
    freeze = output / "plan/freeze.json"
    original = freeze.read_bytes()
    for field, mutate in (
        ("schedule", lambda value: value.__setitem__("schedule", value["schedule"][:2])),
        ("limits", lambda value: value["limits"].__setitem__("repeats", 2)),
        ("same_bundle", lambda value: value.__setitem__(
            "same_bundle", not value["same_bundle"])),
        ("exam", lambda value: value["exam"].__setitem__("grader", "0" * 64)),
    ):
        with pytest.raises(ValueError, match="Frozen"):
            value = json.loads(original)
            mutate(value)
            write_json(freeze, value)
            forged = sha256(freeze)
            assert forged != authorization
            compare.execute(output, forged, client=FixedOutputClient(tmp_path))
        assert not (output / "results/start.json").exists()
        with pytest.raises(ValueError, match="Frozen"):
            compare.report(output)
        freeze.write_bytes(original)
        assert compare.load_plan(output) == plan


def test_rewritten_result_and_receipt_is_regraded_by_the_trusted_verifier(tmp_path):
    config, repo = configuration(tmp_path, ids=("one",))
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    client = FixedOutputClient(tmp_path, {("one", role): "incorrect" for role in compare.ROLES})
    original = compare.execute(output, authorization, client=client)
    assert original["counts"]["unchanged_incorrect"] == 1
    directory = output / "results" / "one-baseline-1"
    saved = (directory / "result.json").read_bytes()
    record = json.loads(saved)
    record.update(verdict="PASS", checks={"passed": True, "assertions": {"fabricated": True}})
    write_json(directory / "result.json", record)
    # A rewritten tree can also carry a consistent receipt; that alone proves nothing.
    write_json(directory / "receipt.json", {"result_sha256": sha256(directory / "result.json")})
    entry = {"task_id": "one", "condition": "baseline", "repeat": 1, "split": "holdout"}
    assert compare.retained_trial(output, entry)["verdict"] == "PASS"
    with pytest.raises(ValueError, match="trusted grader"):
        compare.report(output)
    # Restoring the record does not restore the grade: the receipt is proof of bytes only.
    (directory / "result.json").write_bytes(saved)
    downgraded = compare.report(output)
    trial = downgraded["pairs"][0]["baseline"]
    assert trial["verdict"] == "INCONCLUSIVE"
    assert trial["evaluation_error"] == "retained_trial_record_hash_mismatch"
    assert downgraded["counts"]["unchanged_incorrect"] == 0
    write_json(directory / "receipt.json", {"result_sha256": sha256(directory / "result.json")})
    assert compare.report(output) == original


def test_partial_usage_stops_the_schedule_when_the_next_trial_would_be_unauthorized(tmp_path):
    # token_stop_new_trials bounds a budget that is unknown, so no further trial
    # may start once a call reports usage the controller cannot account for.
    config, repo = configuration(tmp_path, ids=("one", "two"), repeats=1)
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    client = FixedOutputClient(tmp_path, {("one", "baseline"): "unknown_usage"})
    result = compare.execute(output, authorization, client=client)
    assert len(client.calls) == 1
    assert result["status"]["stop_reason"] == "unknown_usage"
    assert result["status"]["execution_state"] == "stopped"
    assert result["status"]["recorded_trials"] == 1
    assert result["status"]["observed_tokens"] == 0
    assert result["counts"]["missing_pair"] == 2


def test_full_known_usage_schedule_still_runs_to_completion(tmp_path):
    config, repo = configuration(tmp_path, ids=("one",), repeats=2)
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    client = FixedOutputClient(tmp_path)
    result = compare.execute(output, authorization, client=client)
    assert len(client.calls) == 4
    assert result["status"]["stop_reason"] is None
    assert result["status"]["execution_state"] == "completed"


def test_modified_trial_verdicts_and_checks_cannot_override_retained_grades(tmp_path):
    config, repo = configuration(tmp_path, ids=("one",))
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    client = FixedOutputClient(tmp_path, {("one", role): "incorrect" for role in compare.ROLES})
    original = compare.execute(output, authorization, client=client)
    saved = (output / "report.json").read_bytes(), (output / "report.md").read_bytes()
    assert original["counts"]["unchanged_incorrect"] == 1
    directories = {role: output / "results" / f"one-{role}-1" for role in compare.ROLES}
    records = {role: (directory / "result.json").read_bytes()
               for role, directory in directories.items()}
    for roles in (("candidate",), ("baseline",), compare.ROLES):
        for field in ("verdict", "checks", "both"):
            for role in roles:
                record = json.loads(records[role])
                if field in ("verdict", "both"):
                    record["verdict"] = "PASS"
                if field in ("checks", "both"):
                    record["checks"] = {"passed": True, "assertions": {"fabricated": True}}
                write_json(directories[role] / "result.json", record)
            result = compare.report(output)
            assert result["counts"]["inconclusive"] == 1
            assert result["counts"]["fix"] == result["counts"]["regression"] == 0
            assert result["counts"]["unchanged_correct"] == 0
            for role in roles:
                trial = result["pairs"][0][role]
                assert trial["verdict"] == "INCONCLUSIVE"
                assert trial["checks"] is None
                assert trial["evaluation_error"] == "retained_trial_record_hash_mismatch"
                assert result["conditions"][role]["evaluation_errors"] == 1
                grader = json.loads((directories[role] / "verification/result.json").read_text())
                assert grader["passed"] is False
            assert compare.report(output) == result
            for role in roles:
                (directories[role] / "result.json").write_bytes(records[role])
            assert compare.report(output) == original
            assert saved == ((output / "report.json").read_bytes(),
                             (output / "report.md").read_bytes())
    path = directories["candidate"] / "result.json"
    malformed = (b"{broken", b"null",
                 json.dumps({**json.loads(records["candidate"]), "observations": None}).encode(),
                 json.dumps({**json.loads(records["candidate"]),
                             "evidence_sha256": {"execution.json": float("nan")}}).encode())
    for mutation in malformed:
        path.write_bytes(mutation)
        result = compare.report(output)
        trial = result["pairs"][0]["candidate"]
        assert trial["verdict"] == "INCONCLUSIVE" and trial["checks"] is None
        assert trial["evaluation_error"] == "retained_trial_record_hash_mismatch"
        assert trial["observations"]["usage"] is None and trial["elapsed_seconds"] is None
        assert trial["output"] is not None and "result.json" in trial["evidence"]
        assert path.read_bytes() == mutation
        assert compare.report(output) == result
        path.write_bytes(records["candidate"])
        assert compare.report(output) == original
    receipt = directories["candidate"] / "receipt.json"
    content = receipt.read_bytes()
    invalid_receipts = ((None, "missing_retained_trial_receipt"),
                        (b"{broken", "invalid_retained_trial_receipt"),
                        (b'{"result_sha256": "changed"}', "retained_trial_record_hash_mismatch"))
    for mutation, error in invalid_receipts:
        if mutation is None:
            receipt.unlink()
        else:
            receipt.write_bytes(mutation)
        result = compare.report(output)
        trial = result["pairs"][0]["candidate"]
        assert trial["verdict"] == "INCONCLUSIVE" and trial["checks"] is None
        assert trial["evaluation_error"] == error
        assert result["counts"]["inconclusive"] == 1
        assert (directories["candidate"] / "result.json").read_bytes() == records["candidate"]
        receipt.write_bytes(content)
        assert compare.report(output) == original
    assert len(client.calls) == 2


def test_retained_answer_integrity_changes_invalidate_positive_pairs(tmp_path):
    config, repo = configuration(tmp_path, ids=("fix", "same"))
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    client = FixedOutputClient(tmp_path, {("fix", "baseline"): "incorrect"})
    original = compare.execute(output, authorization, client=client)
    assert original["counts"]["fix"] == original["counts"]["unchanged_correct"] == 1
    for case, role in (("fix", "candidate"), ("same", "baseline")):
        directory = output / "results" / f"{case}-{role}-1"
        answer = directory / "answer.json"
        content = answer.read_bytes()
        record = (directory / "result.json").read_bytes()
        for mutation, error in ((None, "missing_retained_output"),
                                (content + b" ", "retained_output_hash_mismatch")):
            if mutation is None:
                answer.unlink()
            else:
                answer.write_bytes(mutation)
            result = compare.report(output)
            pair = next(p for p in result["pairs"] if p["case_id"] == case)
            assert pair["outcome"] == "inconclusive"
            assert pair[role]["verdict"] == "INCONCLUSIVE"
            assert pair[role]["evaluation_error"] == error
            assert result["conditions"][role]["evaluation_errors"] == 1
            assert result["counts"]["fix" if case == "fix" else "unchanged_correct"] == 0
            assert result["counts"]["regression"] == 0
            assert (directory / "result.json").read_bytes() == record
            answer.write_bytes(content)
            assert compare.report(output) == original
    assert len(client.calls) == 4


def test_retained_grader_execution_and_trace_evidence_cannot_go_missing_or_change(tmp_path):
    config, repo = configuration(tmp_path, ids=("fix", "same"))
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    client = FixedOutputClient(tmp_path, {("fix", "baseline"): "incorrect"})
    original = compare.execute(output, authorization, client=client)
    saved = (output / "report.json").read_bytes(), (output / "report.md").read_bytes()
    assert original["counts"]["fix"] == original["counts"]["unchanged_correct"] == 1
    for case, role in (("fix", "candidate"), ("same", "baseline")):
        directory = output / "results" / f"{case}-{role}-1"
        record = (directory / "result.json").read_bytes()
        trial = next(p for p in original["pairs"] if p["case_id"] == case)[role]
        assert set(trial["evidence_sha256"]) == set(compare.EVIDENCE)
        assert trial["evidence_sha256"] == trial["retained_evidence_sha256"]
        for name in compare.EVIDENCE:
            artifact = directory / name
            content = artifact.read_bytes()
            for mutation, error in ((None, "missing_retained_evidence"),
                                    (content + b" ", "retained_evidence_hash_mismatch")):
                if mutation is None:
                    artifact.unlink()
                else:
                    artifact.write_bytes(mutation)
                result = compare.report(output)
                pair = next(p for p in result["pairs"] if p["case_id"] == case)
                assert pair["outcome"] == "inconclusive"
                assert pair[role]["verdict"] == "INCONCLUSIVE"
                assert pair[role]["evaluation_error"] == error
                assert result["conditions"][role]["evaluation_errors"] == 1
                assert result["counts"]["fix" if case == "fix" else "unchanged_correct"] == 0
                assert result["counts"]["regression"] == 0
                assert (directory / "result.json").read_bytes() == record
                artifact.write_bytes(content)
                assert compare.report(output) == original
                assert saved == ((output / "report.json").read_bytes(),
                                 (output / "report.md").read_bytes())
    # Older records without integrity metadata cannot substantiate a positive verdict.
    path = output / "results/fix-candidate-1/result.json"
    record = json.loads(path.read_text())
    record.pop("evidence_sha256")
    write_json(path, record)
    (path.parent / "receipt.json").unlink()
    result = compare.report(output)
    assert result["pairs"][0]["candidate"]["evaluation_error"] == "missing_retained_trial_receipt"
    assert result["counts"]["fix"] == 0
    assert len(client.calls) == 4


@pytest.mark.parametrize("mode", ["addition", "update"])
def test_baseline_predating_skill_library_is_allowed_only_for_addition(tmp_path, mode):
    config, source = configuration(tmp_path, mode, ids=("one",))
    repo = tmp_path / "new-library"
    repo.mkdir()
    git(repo, "init", "--quiet")
    (repo / "seed.txt").write_text("Before any skills", encoding="utf-8")
    git(repo, "add", "seed.txt")
    git(repo, "commit", "--quiet", "-m", "Fixture before skill library")
    baseline = git(repo, "rev-parse", "HEAD")
    shutil.copytree(source / "library", repo / "library")
    git(repo, "add", "library")
    git(repo, "commit", "--quiet", "-m", "Fixture first skill library")
    raw = json.loads(config.read_text())
    raw["snapshots"] = {"baseline": {"git_revision": baseline},
                        "candidate": {"git_revision": git(repo, "rev-parse", "HEAD")}}
    write_json(config, raw)
    output = tmp_path / "run"
    if mode == "update":
        with pytest.raises(ValueError, match="Snapshot skill_root contains no files"):
            compare.prepare(config, repo, output)
        return
    authorization = compare.prepare(config, repo, output)
    plan = compare.load_plan(output)
    assert plan["arms"]["baseline"] == {"git_revision": baseline, "bundle": None}
    assert not (output / "plan/bundles/baseline").exists()
    assert (output / "plan/bundles/candidate/selected/resource.txt").read_bytes() == b"new resource"
    client = FixedOutputClient(tmp_path)
    result = compare.execute(output, authorization, client=client)
    assert result["counts"]["unchanged_correct"] == 1
    assert len(client.calls) == 2
    assert set(client.calls[0]["inputs"]) == {"task.json"}
    assert "selected" not in client.calls[0]["prompt"]
    assert compare.report(output) == result


def test_git_replacement_refs_do_not_substitute_snapshot_or_diff(tmp_path):
    config, repo = configuration(tmp_path, ids=("one",))
    raw = json.loads(config.read_text())
    before, after = (raw["snapshots"][role]["git_revision"] for role in compare.ROLES)
    resource = ":library/selected/resource.txt"
    old_blob = git(repo, "rev-parse", before + resource)
    new_blob = git(repo, "rev-parse", after + resource)
    git(repo, "replace", before, after)
    git(repo, "replace", old_blob, new_blob)
    assert "New body" in git(repo, "show", before + ":library/selected/SKILL.md")
    output = tmp_path / "run"
    compare.prepare(config, repo, output)
    plan = compare.load_plan(output)
    baseline = output / "plan/bundles/baseline/selected"
    assert "Old body" in (baseline / "SKILL.md").read_text(encoding="utf-8")
    assert (baseline / "resource.txt").read_bytes() == b"old resource"
    assert any("selected/SKILL.md" in change for change in plan["source_changes"])
    assert plan["arms"]["baseline"]["git_revision"] == before


def test_changed_runtime_controllers_reject_execution_and_report_without_calls(tmp_path):
    # Load a real controller copy so filesystem changes cannot affect concurrent tests.
    runtime = tmp_path / "runtime"
    for name in (*compare.CONTROLLERS, "tools/skill_eval_verifier.py"):
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    spec = importlib.util.spec_from_file_location(
        "comparison_runtime", runtime / "tools/skill_eval_compare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config, repo = configuration(tmp_path, ids=("one",))
    output = tmp_path / "run"
    authorization = module.prepare(config, repo, output)
    module.report(output)
    saved = (output / "report.json").read_bytes(), (output / "report.md").read_bytes()
    assert "src/inferrsi/harness/local_process.py" in module.CONTROLLERS
    assert "src/inferrsi/infra/artifacts.py" in module.CONTROLLERS
    assert "tools/skill_eval_artifact_guidance.py" in module.CONTROLLERS
    # Each owner of the split trial path is frozen: composed entry, rules and host.
    for owner in ("apps", "benchmark", "harness"):
        assert f"src/inferrsi/{owner}/skill_evaluation.py" in module.CONTROLLERS
    for name in ("apps/skill_eval_engineering.py", "benchmark/skill_eval_engineering.py",
                 "benchmark/skill_eval_diagnostic.py", "harness/artifact_sandbox.py",
                 "harness/atomic_json.py", "harness/input_binding.py"):
        assert "src/inferrsi/" + name in module.CONTROLLERS
    assert not any("harness/skill_eval_" in name for name in module.CONTROLLERS)
    client = FixedOutputClient(tmp_path)
    for name in module.CONTROLLERS:
        target = runtime / name
        original = target.read_bytes()
        target.write_bytes(original + b"\n# Changed after the plan was frozen.\n")
        with pytest.raises(ValueError, match="Controller changed after freeze"):
            module.execute(output, authorization, client=client)
        with pytest.raises(ValueError, match="Controller changed after freeze"):
            module.report(output)
        assert not (output / "results/start.json").exists()
        assert saved == ((output / "report.json").read_bytes(), (output / "report.md").read_bytes())
        target.write_bytes(original)
    assert client.calls == []


@pytest.mark.parametrize("extra", [
    {"answer": "hidden"}, {"expected": "hidden"}, {"expected_answer": "hidden"},
    {"oracle": {"answer": "hidden"}}, {"metadata": {"answer": "hidden"}},
    {"contract": {"min_reduction_pct": 20, "oracle": "hidden"}},
    {"evidence_keys": [{"answer": "hidden"}]},
    {"candidate": {"workload": {"steps": 8, "metadata": {"answer": "hidden"}}}},
])
def test_answer_keys_are_rejected_by_supported_input_shape(tmp_path, extra):
    config, repo = configuration(tmp_path, ids=("one",))
    cases = tmp_path / "cases.json"
    tasks = json.loads(cases.read_text())
    tasks[0].update(extra)
    write_json(cases, tasks)
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="input"):
        compare.prepare(config, repo, output)
    assert not output.exists()


def test_unverifiable_custom_grader_cannot_produce_a_pair_outcome(tmp_path):
    # A custom grader has no in-tree authority to re-derive from, and the retained
    # record is writable by the graded agent. Its stated PASS/FAIL must not become
    # a fix, regression or unchanged outcome.
    config, repo = configuration(tmp_path, ids=("one",))
    raw = json.loads(config.read_text())
    # Same contract, different bytes: the in-tree verifier cannot be identified
    # by hash, so this exam has no trusted re-derivation.
    grader = tmp_path / "custom_verifier.py"
    grader.write_text(
        (ROOT / "tools/skill_eval_verifier.py").read_text(encoding="utf-8")
        + "\n# custom exam marker\n",
        encoding="utf-8",
    )
    raw["grader"] = grader.name
    write_json(config, raw)
    output = tmp_path / "run"
    authorization = compare.prepare(config, repo, output)
    result = compare.execute(output, authorization, client=FixedOutputClient(tmp_path))
    trial = result["pairs"][0]["baseline"]
    assert result["counts"]["inconclusive"] == 1
    assert result["counts"]["unchanged_correct"] == 0
    assert trial["verdict"] == "INCONCLUSIVE"
    assert trial["stated_verdict"] in ("PASS", "FAIL")
    assert trial["grader_trust"] == "unverified"
    assert trial["evaluation_error"] == "unverified_custom_grader"


def test_forged_identical_bundle_claim_is_rejected_against_exported_bytes(tmp_path):
    # Both sides of the identical-bundle invariant come from mutable freeze
    # metadata. Retargeting only the candidate revision must not make two
    # different exported bundles read as the same skill content.
    config, repo = configuration(tmp_path, mode="update", ids=("one",))
    output = tmp_path / "run"
    compare.prepare(config, repo, output)
    plan = compare.load_plan(output)
    assert plan["same_bundle"] is False
    assert plan["arms"]["baseline"]["bundle"]["revision"] != (
        plan["arms"]["candidate"]["bundle"]["revision"])
    freeze = output / "plan/freeze.json"
    forged = json.loads(freeze.read_text(encoding="utf-8"))
    forged["arms"]["candidate"]["bundle"]["revision"] = (
        forged["arms"]["baseline"]["bundle"]["revision"])
    forged["same_bundle"] = True
    write_json(freeze, forged)
    with pytest.raises(ValueError, match="identical-bundle"):
        compare.load_plan(output)
