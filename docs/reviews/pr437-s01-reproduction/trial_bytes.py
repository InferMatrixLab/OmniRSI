import argparse
import copy
import hashlib
import importlib
import json
import shutil
import sys
import time
from pathlib import Path
from unittest import mock

# Fixed-fixture trial records for the S01 before/after byte comparison.
# Module names are arguments so one script runs on both tree layouts.

FAULTS = ("exception", "missing", "invalid_json", "nonobject", "missing_passed",
          "string_passed", "missing_assertions", "empty_assertions", "string_assertion",
          "inconsistent", "wrong_exit", "nan_assertion", "infinite_assertion",
          "overflow_assertion")
# Independently authored diagnostic answers, same as tests/test_skill_eval_diagnostic.py.
DIAGNOSTIC_ANSWERS = (
    ("readiness", ["E2"], "check_receive_completion"),
    ("capacity", ["E2", "E3", "E4"], "inspect_allocator_accounting"),
    ("invalid_reuse", ["E2"], "check_transfer_integrity"),
    ("insufficient_evidence", ["E1"], "collect_missing_events"),
    ("readiness", ["E2"], "check_receive_completion"),
    ("capacity", ["E2", "E3", "E4"], "inspect_allocator_accounting"),
    ("invalid_reuse", ["E3"], "check_transfer_integrity"),
    ("insufficient_evidence", ["E2", "E3"], "collect_missing_events"),
    ("insufficient_evidence", ["E2", "E3"], "collect_missing_events"),
    ("invalid_reuse", ["E2"], "check_transfer_integrity"),
)
ELAPSED = 1.2345678901234567


def dump(value) -> bytes:
    # Same encoding as the retained JSON evidence; key order is not sorted.
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


class FixtureClient:
    def __init__(self, fixtures: Path, mode: str, answer: Path, timeout_seconds: float = 20):
        self.fixtures, self.mode, self.answer = fixtures, mode, answer
        self.timeout_seconds = timeout_seconds

    def command(self, workspace, final, schema, prompt):
        return (sys.executable, str(self.fixtures / "skill_eval_fake_client.py"),
                self.mode, str(final), str(self.answer))


def stable_executor(real):
    def execute(*args, **kwargs):
        record = real(*args, **kwargs)
        # Only host-assigned process facts vary between runs.
        if "pid" in record:
            record["pid"] = 4242
            if record["process_group_id"] is not None:
                record["process_group_id"] = 4242
        record["elapsed_seconds"] = ELAPSED
        return record
    return execute


def audit_task(data, case_id):
    task = copy.deepcopy(data["task"])
    case = next(case for case in data["cases"] if case["id"] == case_id)
    for path, value in case["changes"].items():
        target = task
        *parents, key = path.split(".")
        for parent in parents:
            target = target[parent]
        target[key] = value
    return task, case["answer"]


def run(args):
    repo = Path(args.repo).resolve()
    fixtures = repo / "tests/fixtures"
    entry = importlib.import_module(args.trial_entry)
    executor = importlib.import_module(args.executor)
    engineering = importlib.import_module(args.engineering_task)
    artifact_check = importlib.import_module(args.engineering_verify)
    sandbox = importlib.import_module(args.sandbox)
    diagnostic = importlib.import_module(args.diagnostic)
    improvement = importlib.import_module(args.improvement)
    work = Path(args.work).resolve()
    output = Path(args.output).resolve()
    for folder in (work, output):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
    # Fixed fixture locations keep recorded command lines independent of the tree path.
    client_fixtures = work / "_fixtures"
    client_fixtures.mkdir()
    shutil.copyfile(fixtures / "skill_eval_fake_client.py",
                    client_fixtures / "skill_eval_fake_client.py")
    grading = json.loads((fixtures / "skill_eval_grading.json").read_text(encoding="utf-8"))
    cases = json.loads((fixtures / "skill_eval_diagnostic_cases.json").read_text(encoding="utf-8"))
    records = []

    def trial(name, task, answer, verifier, *, mode="correct", condition="N", timeout=20,
              starter=None, **options):
        root = work / name
        workspace = root / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "task.json").write_text(json.dumps(task), encoding="utf-8")
        if starter is not None:
            (workspace / "parser.py").write_bytes(starter)
        answer_path = root / "expected-answer.json"
        answer_path.write_text(json.dumps(answer), encoding="utf-8")
        schema = root / "schema.json"
        schema.write_text('{"type":"object"}', encoding="utf-8")
        # Same as the comparison tool: the frozen grader copy runs, not its source path.
        (root / "validator").mkdir()
        frozen = root / "validator/verify.py"
        shutil.copyfile(verifier, frozen)
        record = entry.run_trial(
            FixtureClient(client_fixtures, mode, answer_path, timeout), task, condition, 1,
            workspace, root / "trial", schema, frozen, "Offline fixture only",
            time.monotonic() + 45,
            **options)
        records.append(record)
        for path in sorted((root / "trial").rglob("*")):
            if path.is_file():
                target = output / name / path.relative_to(root / "trial")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)

    verifier = repo / "tools/skill_eval_verifier.py"
    with mock.patch.object(executor, "execute_command",
                           stable_executor(executor.execute_command)):
        for case_id in ("equal-threshold", "quality-failed", "kernel-only"):
            for condition in ("N", "E-benchmark"):
                task, answer = audit_task(grading, case_id)
                trial(f"audit-pass-{case_id}-{condition}", task, answer, verifier,
                      condition=condition)
        for mode in ("incorrect", "null", "nonobject", "nonzero", "missing_output",
                     "invalid_json", "invalid_trace", "unknown_usage", "partial_usage"):
            task, answer = audit_task(grading, "equal-threshold")
            task["id"] = mode
            trial(f"audit-client-{mode}", task, answer, verifier, mode=mode)
        task, answer = audit_task(grading, "equal-threshold")
        trial("audit-client-timeout", {**task, "id": "timeout"}, answer, verifier,
              mode="timeout", timeout=1)
        for fault in FAULTS:
            task, answer = audit_task(grading, "equal-threshold")
            task.update(id="fault-" + fault, verifier_fault=fault)
            trial(f"audit-verifier-{fault}", task, answer,
                  fixtures / "skill_eval_faulty_verifier.py")
        task, answer = audit_task(grading, "equal-threshold")
        task["baseline"]["latencies_ms"] = [[0]]
        trial("audit-invalid-input-report", {**task, "id": "invalid-input"}, answer, verifier)

        grader = Path(diagnostic.__file__)
        for index, case in enumerate(cases):
            diagnosis, evidence, check = DIAGNOSTIC_ANSWERS[index]
            answer = {"diagnosis": diagnosis, "evidence": evidence, "next_check": check,
                      "reuse_authorized": False}
            trial(f"diagnostic-pass-{case['id']}", case, answer, grader)
            trial(f"diagnostic-fail-{case['id']}", case, {**answer, "reuse_authorized": True},
                  grader, condition="E-diagnostic")

        task = {"id": "serving-parser", "split": "development", "request": engineering.REQUEST,
                "provenance": "Reconstructed public regression fixture"}
        starter = engineering.STARTER.encode("utf-8")
        answer = {"artifact": "parser.py", "summary": "Repair"}
        expected = [{"success": success, "reason": reason}
                    for _, _, _, _, success, reason in engineering.VECTORS]

        def as_starter(work_dir, arguments=None, *, probe=False):
            if probe:
                return {"ready": True}
            return {"success": arguments[0] == 200,
                    "reason": "length" if arguments[0] == 200 else "http_error"}

        trial("engineering-sandbox-unavailable", task, answer, verifier, starter=starter,
              engineering_artifact=True)
        with mock.patch.object(sandbox, "_invoke", side_effect=[{"ready": True}, *expected]):
            trial("engineering-verified-pass", task, answer, verifier, starter=starter,
                  engineering_artifact=True)
        with mock.patch.object(sandbox, "_invoke", side_effect=as_starter):
            trial("engineering-verified-fail", task, answer, verifier, starter=starter,
                  engineering_artifact=True, condition="E-engineering")
        trial("engineering-invalid-answer", task, {**answer, "passed": True}, verifier,
              starter=starter, engineering_artifact=True)
        trial("engineering-missing-artifact", task, answer, verifier, engineering_artifact=True)
        source = "def evaluate(status, frames, requested_tokens):\n    return {}\n"
        trial("generation-deferred", task, {**answer, "artifact_source": source}, verifier,
              starter=starter, engineering_artifact=True, generation_only=True)
        trial("generation-invalid", task, {**answer, "artifact_source": ""}, verifier,
              starter=starter, engineering_artifact=True, generation_only=True)

    pure = {"summarize_trial_records": entry.summarize(records)}
    usage = [None, {"input_tokens": 17}, {"input_tokens": 17, "output_tokens": 3},
             "malformed", {"input_tokens": -1, "output_tokens": 0.5}]
    synthetic = [
        {"task_id": f"t{index % 3}", "repeat": 1 + index // 6, "condition": condition,
         "verdict": verdict, "elapsed_seconds": 0.1 * (index + 1),
         "observations": {"usage": usage[index % len(usage)]},
         **({"evaluation_error": "agent_failed"} if verdict == "INCONCLUSIVE" else {})}
        for index, (condition, verdict) in enumerate(
            (condition, verdict)
            for condition in ("N", "E-benchmark", "E-evidence")
            for verdict in ("PASS", "FAIL", "INCONCLUSIVE", "FAIL", "PASS", "PASS"))
    ]
    pure["summarize_synthetic"] = entry.summarize(synthetic)

    def arm(verdict, tokens, calls=2, elapsed=0.25):
        return {"verdict": verdict, "elapsed_seconds": elapsed,
                "observations": {"usage": tokens, "tool_calls": calls}}

    def report(candidates, baselines=("PASS", "FAIL", "FAIL"), tokens=100, state="completed",
               candidate_usage=None):
        outcomes = {("FAIL", "PASS"): "fix", ("PASS", "FAIL"): "regression",
                    ("PASS", "PASS"): "unchanged_correct", ("FAIL", "FAIL"): "unchanged_incorrect"}
        pairs = []
        for index, (before, after) in enumerate(zip(baselines, candidates)):
            used = {"input_tokens": tokens, "output_tokens": 7, "cached_input_tokens": 3}
            pairs.append({
                "case_id": f"case-{index}", "repeat": 1,
                "outcome": outcomes.get((before, after), "inconclusive"),
                "baseline": arm(before, {"input_tokens": 100, "output_tokens": 7}),
                "candidate": arm(after, used if candidate_usage is None else candidate_usage)})
        return {"status": {"execution_state": state}, "pairs": pairs}

    reports = {
        "fix_cheaper": report(("PASS", "PASS", "FAIL"), tokens=80),
        "fix_all": report(("PASS", "PASS", "PASS"), tokens=120),
        "same_cheaper": report(("PASS", "FAIL", "FAIL"), tokens=60),
        "same_equal": report(("PASS", "FAIL", "FAIL"), tokens=100),
        "regression": report(("FAIL", "PASS", "PASS"), tokens=50),
        "worse": report(("FAIL", "FAIL", "FAIL"), tokens=10),
        "incomplete": report(("PASS", "PASS", "FAIL"), state="stopped"),
        "inconclusive": report(("PASS", "INCONCLUSIVE", "FAIL")),
        "unknown_usage": report(("PASS", "PASS", "FAIL"), candidate_usage="malformed"),
        "other_baseline": report(("PASS", "PASS", "PASS"), baselines=("FAIL", "FAIL", "PASS")),
        "empty": {"status": {"execution_state": "completed"}, "pairs": []},
    }
    policies = {"strict": improvement.SelectionPolicy(0.5, 0),
                "loose": improvement.SelectionPolicy(0, 3),
                "high": improvement.SelectionPolicy(1.0, 0)}
    judged = {}
    for policy_name, policy in policies.items():
        for name, value in reports.items():
            judged[f"assessment/{policy_name}/{name}"] = improvement.assessment(value, policy)
            judged[f"holdout_accepts/{policy_name}/{name}"] = improvement.holdout_accepts(
                value, policy)
        for first, second in (("fix_cheaper", "fix_all"), ("fix_cheaper", "same_cheaper"),
                              ("fix_cheaper", "regression"), ("fix_cheaper", "incomplete"),
                              ("fix_cheaper", "unknown_usage"), ("fix_cheaper", "other_baseline"),
                              ("worse", "same_equal"), ("fix_all", "fix_cheaper")):
            probes = {"inv": reports[first], "dom": reports[second]}
            for composable in (False, True):
                judged[f"choose/{policy_name}/{first}+{second}/{composable}"] = (
                    improvement.choose(probes, policy, composable=composable))
            for rerun in ("fix_cheaper", "fix_all", "same_cheaper", "worse", "other_baseline"):
                judged[f"rerun/{policy_name}/{first}+{second}/{rerun}"] = (
                    improvement.rerun_assessment(reports[rerun], probes, policy))
        judged[f"choose/{policy_name}/single"] = improvement.choose(
            {"inv": reports["fix_cheaper"]}, policy, composable=False)
        judged[f"choose/{policy_name}/none"] = improvement.choose({}, policy, composable=False)
    pure["improvement"] = judged

    graded = {}
    for index, case in enumerate(cases):
        diagnosis, evidence, check = DIAGNOSTIC_ANSWERS[index]
        answer = {"diagnosis": diagnosis, "evidence": evidence, "next_check": check,
                  "reuse_authorized": False}
        expected_diagnosis, required, next_check = diagnostic.expected(case)
        graded[case["id"]] = {
            "expected": [expected_diagnosis, sorted(required), next_check],
            "correct": diagnostic.verify(case, answer),
            "unsafe": diagnostic.verify(case, {**answer, "reuse_authorized": True}),
            "wrong_citation": diagnostic.verify(case, {**answer, "evidence": ["E999"]}),
            "malformed": diagnostic.verify(case, {"diagnosis": []}),
        }
    pure["diagnostic"] = graded

    task = {"id": "serving-parser", "split": "development", "request": engineering.REQUEST,
            "provenance": "Reconstructed public regression fixture"}
    answer = {"artifact": "parser.py", "summary": "Repair", "artifact_source": engineering.STARTER}
    expected = [{"success": success, "reason": reason}
                for _, _, _, _, success, reason in engineering.VECTORS]
    verified = {}
    with mock.patch.object(sandbox, "_invoke", side_effect=[{"ready": True}, *expected]):
        verified["pass"] = artifact_check.verify(task, answer)
    wrong = copy.deepcopy(expected)
    wrong[4] = {"success": True, "reason": "length"}
    wrong[7] = {"success": 1, "reason": "token_mismatch"}
    wrong[9] = None
    with mock.patch.object(sandbox, "_invoke", side_effect=[{"ready": True}, *wrong]):
        verified["fail"] = artifact_check.verify(task, answer)
    with mock.patch.object(sandbox, "_invoke") as invoke:
        verified["schema"] = artifact_check.verify(task, {**answer, "artifact": "other.py"})
        verified["empty"] = artifact_check.verify(task, {**answer, "artifact_source": ""})
        verified["oversized"] = artifact_check.verify(
            task, {**answer, "artifact_source": "x" * 65537})
        assert not invoke.called
    pure["engineering"] = verified
    pure["constants"] = {
        "engineering": {name: getattr(engineering, name) for name in (
            "ADAPTER", "GENERATION_MODE", "MAX_SOURCE_BYTES", "STARTER", "SCHEMA", "PROMPT",
            "GENERATION_SCHEMA", "GENERATION_PROMPT", "REQUEST", "REASONS", "VECTORS")},
        "diagnostic": {name: getattr(diagnostic, name) for name in (
            "ADAPTER", "PROMPT", "DIAGNOSES", "CHECKS", "SCHEMA")},
        "runner": sandbox.RUNNER,
    }
    for name, value in pure.items():
        (output / "pure").mkdir(exist_ok=True)
        (output / "pure" / f"{name}.json").write_bytes(dump(value))

    manifest = {path.relative_to(output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(output.rglob("*")) if path.is_file()}
    (output / "manifest.json").write_bytes(dump({
        "modules": {"trial_entry": args.trial_entry, "executor": args.executor,
                    "engineering_task": args.engineering_task,
                    "engineering_verify": args.engineering_verify, "sandbox": args.sandbox,
                    "diagnostic": args.diagnostic, "improvement": args.improvement},
        "trial_records": len(records), "files": manifest}))
    print(f"{len(records)} trial records, {len(manifest)} files -> {output}")


def compare(args):
    before = json.loads((Path(args.before) / "manifest.json").read_text(encoding="utf-8"))["files"]
    after = json.loads((Path(args.after) / "manifest.json").read_text(encoding="utf-8"))["files"]
    different = sorted(name for name in before.keys() | after.keys()
                       if before.get(name) != after.get(name))
    records = sorted(name for name in before if name.endswith("/result.json")
                     and not name.startswith("pure/") and "/verification/" not in name)
    print(f"files: before={len(before)} after={len(after)} different={len(different)}")
    print(f"trial result.json records compared: {len(records)}")
    for name in different:
        print("DIFFERENT", name, before.get(name), after.get(name))
    raise SystemExit(1 if different else 0)


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("run")
    capture.add_argument("--repo", required=True)
    capture.add_argument("--work", required=True)
    capture.add_argument("--output", required=True)
    for name in ("trial-entry", "executor", "engineering-task", "engineering-verify",
                 "sandbox", "diagnostic", "improvement"):
        capture.add_argument("--" + name, required=True)
    capture.set_defaults(handler=run)
    check = commands.add_parser("compare")
    check.add_argument("before")
    check.add_argument("after")
    check.set_defaults(handler=compare)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
