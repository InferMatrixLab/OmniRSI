import argparse
import copy
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from inferrsi.apps.skill_evaluation import export_bundle, run_trial, summarize
from inferrsi.harness.skill_evaluation import CodexClient
from inferrsi.harness.usage import normalize_usage
from inferrsi.infra.artifacts import sha256, write_json
from inferrsi.knowledge_operations import KnowledgeReader

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("inference-benchmark-design", "dense-performance-evidence")
PROMPT = (
    "Read task.json and perform its requested operation. Treat task records and its frozen "
    "contract as authoritative for this exercise; knowledge is fallible guidance. Return only "
    "the required JSON. Preserve the distinction between the report's verdict and your own "
    "task completion. Inspect only this workspace. Do not use network, modify inputs, "
    "run another agent, commit or publish. Evidence entries must use exact evidence_keys."
)
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "report_verdict",
        "latency_reduction_pct",
        "findings",
        "claim_scope",
        "evidence",
        "reason",
    ],
    "properties": {
        "report_verdict": {
            "type": ["string", "null"],
            "enum": ["PASS", "FAIL", "INCONCLUSIVE", None],
        },
        "latency_reduction_pct": {"type": ["number", "null"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "workload_mismatch",
                    "unsupported_service_claim",
                    "quality_missing",
                    "quality_failed",
                    "completion_gate_failed",
                    "failed_requests_omitted",
                    "gain_gate_failed",
                ],
            },
        },
        "claim_scope": {"type": "string", "enum": ["none", "kernel_only", "fixed_workload"]},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
}


def fixtures(final_only=False):
    source = json.loads(
        (ROOT / "evidence/qwen21-turbo-b300-review-20261012/results/summary.json").read_text(
            encoding="utf-8"
        )
    )
    arms = list(source["arms"].values())
    base = {
        "id": "dev-valid",
        "split": "development",
        "mode": "audit",
        "provenance": "Historical latency slices plus explicitly counterfactual test metadata; "
        "this exercise is not a new hardware measurement.",
        "request": "Audit the claimed fixed-workload latency acceptance; compute median of "
        "per-trial request means and percentage reduction. Identify only material "
        "contract violations and the strongest supported scope. Do not infer "
        "production/deployment qualification. Explain the smallest next action.",
        "contract": {
            "min_reduction_pct": 20,
            "scope": "fixed_workload",
            "required_regimes": ["C1"],
            "quality_required": True,
            "all_requests_required": True,
            "max_relative_spread_pct": 12,
            "statistical_significance_required": False,
        },
        "evidence_scope": "http_fixed_workload",
        "baseline": {
            "latencies_ms": [[r["latency_ms"] for r in group] for group in arms[0]["requests"]],
            "workload": {"steps": 8, "concurrency": 1, "shape": [1280, 704]},
            "quality": "passed",
            "total_requests": 15,
            "reported_requests": 15,
            "failed_requests": 0,
        },
        "candidate": {
            "latencies_ms": [[r["latency_ms"] for r in group] for group in arms[-1]["requests"]],
            "workload": {"steps": 8, "concurrency": 1, "shape": [1280, 704]},
            "quality": "passed",
            "total_requests": 15,
            "reported_requests": 15,
            "failed_requests": 0,
        },
        "evidence_keys": [
            "contract",
            "baseline.latencies_ms",
            "candidate.latencies_ms",
            "baseline.workload",
            "candidate.workload",
            "candidate.quality",
            "candidate.total_requests",
            "candidate.reported_requests",
            "candidate.failed_requests",
            "evidence_scope",
        ],
    }
    base["contract"]["verdict_definitions"] = {
        "PASS": "Comparable and sufficient evidence satisfies all frozen acceptance gates.",
        "FAIL": "Comparable and sufficient evidence demonstrates a quality, request-completion "
        "or gain gate violation. Do not use FAIL merely to reject an unsupported claim.",
        "INCONCLUSIVE": "Workloads are incomparable, evidence scope is insufficient for the "
        "target claim, or required evidence is missing; the target claim cannot be decided.",
    }
    base["contract"]["output_field_semantics"] = {
        "latency_reduction_pct": "Use null if baseline/candidate workloads differ. Otherwise "
        "return the descriptive median-of-trial-means reduction, even when acceptance fails.",
        "claim_scope": "fixed_workload only for a passing full acceptance; kernel_only when "
        "kernel timings support only that scope; none for other failed/inconclusive acceptance.",
    }
    if final_only:
        base["baseline"]["latencies_ms"] = [
            [r["latency_ms"] for r in group] for group in arms[1]["requests"]
        ]
    result = [base]
    for identity, split in (
        ("dev-kernel", "development"),
        ("holdout-workload", "holdout"),
        ("holdout-quality", "holdout"),
        ("holdout-dropped", "holdout"),
        ("holdout-scoped-valid", "holdout"),
    ):
        task = copy.deepcopy(base)
        task.update(id=identity, split=split)
        if identity == "dev-kernel":
            task["evidence_scope"] = "kernel"
            task["request"] += (
                " The author claims these kernel timings establish HTTP service gain."
            )
        elif identity == "holdout-workload":
            task["candidate"]["workload"]["steps"] = 4
        elif identity == "holdout-quality":
            task["candidate"]["quality"] = "failed"
        elif identity == "holdout-dropped":
            task["candidate"].update(total_requests=18, failed_requests=3)
        elif identity == "holdout-scoped-valid":
            task["contract"]["min_reduction_pct"] = 25
            task["request"] += (
                " The campaign requires only C1; replay and production are outside its claim."
            )
        result.append(task)
    for index, task in enumerate(result):
        task["id"] = ("dev-" if task["split"] == "development" else "holdout-") + str(index + 1)
    return result


def build_schedule(tasks: list[dict], final_only=False) -> list[dict]:
    schedule = []
    for task in tasks:
        if final_only and task["split"] == "development":
            continue
        repeats = 1 if task["split"] == "development" else 2
        if final_only:
            repeats = 2 if task["id"] in {"holdout-4", "holdout-6"} else 1
        for repeat in range(1, repeats + 1):
            conditions = ["N", "E-benchmark", "E-evidence"]
            # Rotate each matched group once; repeat identifies the pair only.
            shift = (len(schedule) // 3) % 3
            for condition in conditions[shift:] + conditions[:shift]:
                schedule.append({"task_id": task["id"], "condition": condition, "repeat": repeat})
    for mode in ("relevant", "ambiguous", "irrelevant"):
        schedule.append({"task_id": "R-" + mode, "condition": "R", "repeat": 1})
    return schedule


def prepare(
    output: Path,
    model: str,
    effort: str,
    protected_roots: list[Path],
    final_only=False,
    campaign_seconds=5400,
    token_limit=3000000,
):
    output.mkdir(parents=True, exist_ok=False)
    for name in ("plan", "validator", "results", "patch"):
        (output / name).mkdir()
    tasks = fixtures(final_only)
    write_json(output / "plan/tasks.json", tasks)
    write_json(output / "plan/schema.json", SCHEMA)
    shutil.copyfile(ROOT / "tools/skill_eval_verifier.py", output / "validator/verify.py")
    reader = KnowledgeReader(skill_root=ROOT / "src/inferrsi/knowledge/data/skill_library")
    bundles = {name: reader.read("checkout:" + name).view() for name in SKILLS}
    schedule = build_schedule(tasks, final_only)
    source_files = [
        ROOT / "src/inferrsi/apps/skill_evaluation.py",
        ROOT / "src/inferrsi/benchmark/skill_evaluation.py",
        ROOT / "src/inferrsi/harness/skill_evaluation.py",
        Path(__file__),
        ROOT / "tools/skill_eval_verifier.py",
    ]
    plan = {
        "base": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "model": model,
        "effort": effort,
        "client": subprocess.check_output(["codex", "--version"], text=True).strip(),
        "timeout_seconds": 180,
        "campaign_seconds": campaign_seconds,
        "max_task_calls": len(schedule),
        "contract_revision": "v2-explicit-verdict-and-field-semantics",
        "phase": "final_after_retained_v1_development" if final_only else "development_and_final",
        "token_stop_new_trials": token_limit,
        "cost": None,
        "retry_policy": "none",
        "seed_control": "not_exposed_by_client",
        "protected_roots": [str(p.resolve()) for p in protected_roots],
        "skills": bundles,
        "schedule": schedule,
        "frozen_files": {str(p.relative_to(ROOT)): sha256(p) for p in source_files},
        "tasks_sha256": sha256(output / "plan/tasks.json"),
        "schema_sha256": sha256(output / "plan/schema.json"),
        "verifier_sha256": sha256(output / "validator/verify.py"),
        "isolation": "Owner-authorized controlled exposure without OS read confinement. "
        "Global/bundled skills, plugins, apps, memories and web disabled. "
        "Fresh ephemeral client/workspace each trial; observed access requires audit.",
        "source_reuse": "Internal reader/executor + sibling pilot methodology; "
        "no external eval code copied.",
    }
    write_json(output / "plan/freeze.json", plan)
    (output / "patch/README.md").write_text("Implementation revision is recorded in the report.\n")
    print(
        json.dumps(
            {
                "freeze_sha256": sha256(output / "plan/freeze.json"),
                "tasks": len(tasks),
                "task_calls": len(schedule),
            }
        )
    )


def recover_attempts(output: Path, schedule: list[dict]) -> list[dict]:
    trials = []
    for entry in schedule:
        directory = (
            output / "results" / (f"{entry['task_id']}-{entry['condition']}-{entry['repeat']}")
        )
        if not directory.is_dir():
            continue
        if (directory / "result.json").is_file():
            record = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            assert all(record[k] == entry[k] for k in ("task_id", "condition", "repeat"))
        else:
            record = {
                **entry,
                "execution_state": "interrupted",
                "verdict": "INCONCLUSIVE",
                "evaluation_error": "interrupted_attempt",
                "elapsed_seconds": None,
                "checks": None,
                "observations": {
                    "usage": None,
                    "commands": [],
                    "completed_turns": 0,
                    "errors": ["interrupted_attempt"],
                },
                "activation_observation": "unknown",
                "loaded_skills": [],
            }
            write_json(directory / "result.json", record)
        trials.append(record)
    return trials


def observed_tokens(usage: dict | None) -> int:
    # Count observed token fields only; report completeness separately in status and summary.
    usage = normalize_usage(usage)
    if usage is None:
        return 0
    return sum(
        value for key, value in usage.items()
        if key in ("input_tokens", "output_tokens") and value is not None
    )


def execute(output: Path, max_new_trials: int | None = None):
    plan = json.loads((output / "plan/freeze.json").read_text(encoding="utf-8"))
    assert sha256(output / "plan/tasks.json") == plan["tasks_sha256"]
    assert sha256(output / "plan/schema.json") == plan["schema_sha256"]
    assert sha256(output / "validator/verify.py") == plan["verifier_sha256"]
    for name, digest in plan["frozen_files"].items():
        assert sha256(ROOT / name) == digest, "Controller changed after freeze"
    client = CodexClient(
        plan["model"],
        plan["effort"],
        plan["timeout_seconds"],
    )
    tasks = {t["id"]: t for t in json.loads((output / "plan/tasks.json").read_text())}
    reader = KnowledgeReader(skill_root=ROOT / "src/inferrsi/knowledge/data/skill_library")
    for name, bundle in plan["skills"].items():
        assert reader.read("checkout:" + name).revision == bundle["revision"]
    trials = recover_attempts(output, plan["schedule"])
    tokens = sum(
        observed_tokens(t["observations"]["usage"]) for t in trials
    )
    prior = {(t["task_id"], t["condition"], t["repeat"]) for t in trials}
    start_path = output / "results/start.json"
    if not start_path.is_file():
        write_json(start_path, {"started_at": time.time()})
    started = json.loads(start_path.read_text())["started_at"]
    deadline = time.monotonic() + max(0, plan["campaign_seconds"] - (time.time() - started))
    new_trials = 0
    stop_reason = None
    for entry in plan["schedule"]:
        if (entry["task_id"], entry["condition"], entry["repeat"]) in prior:
            continue
        if max_new_trials is not None and new_trials >= max_new_trials:
            stop_reason = "operator_batch_limit"
            break
        if time.monotonic() >= deadline or tokens >= plan["token_stop_new_trials"]:
            stop_reason = "time_budget" if time.monotonic() >= deadline else "token_budget"
            break
        task_id, condition, repeat = entry["task_id"], entry["condition"], entry["repeat"]
        if condition == "R":
            task = copy.deepcopy(tasks["dev-2" if task_id == "R-relevant" else "dev-1"])
            task["id"] = task_id
            if task_id == "R-irrelevant":
                task["mode"] = "extract"
                task["request"] = (
                    "Only calculate the median-of-trial-means latency reduction. "
                    "Do not audit or judge acceptance. Return report_verdict=null, empty findings, "
                    "claim_scope=none, and cite the latency arrays."
                )
            elif task_id == "R-ambiguous":
                task["request"] = (
                    "Summarize what these measurements establish within this "
                    "small campaign's scope, without designing a benchmark. Compute the "
                    "latency reduction and give the scoped acceptance verdict."
                )
        else:
            task = tasks[task_id]
        trial_dir = output / "results" / f"{task_id}-{condition}-{repeat}"
        with tempfile.TemporaryDirectory(prefix="inferrsi-eval-") as directory:
            workspace = Path(directory)
            write_json(workspace / "task.json", task)
            prompt = PROMPT + "\nTask operation: " + task["request"]
            prompt += "\nTask input (identical to task.json):\n" + json.dumps(task)
            if condition.startswith("E"):
                name = SKILLS[0 if condition == "E-benchmark" else 1]
                view = export_bundle(reader, "checkout:" + name, workspace / "knowledge")
                prompt += "\nUse the assigned skill at knowledge/" + view["entrypoint"] + "."
                prompt += " If a skill section influences your decision, identify it in reason."
                bundle = reader.read("checkout:" + name)
                for path, data in bundle.content.items():
                    prompt += "\nAssigned resource " + path + ":\n" + data.decode("utf-8")
            elif condition == "R":
                for name in SKILLS:
                    export_bundle(reader, "checkout:" + name, workspace / ".agents/skills")
            result = run_trial(
                client,
                task,
                condition,
                repeat,
                workspace,
                trial_dir,
                output / "plan/schema.json",
                output / "validator/verify.py",
                prompt,
                deadline,
            )
        result["candidate_skill_accesses"] = [
            name
            for name in SKILLS
            if any(
                name in command.get("command", "")
                and "SKILL.md" in command.get("command", "")
                and command.get("exit_code") == 0
                for command in result["observations"]["commands"]
            )
        ]
        result["activation_observation"] = (
            "matching_successful_command_requires_review"
            if result["candidate_skill_accesses"]
            else "unknown"
        )
        result["supplied_skills"] = (
            [SKILLS[0 if condition == "E-benchmark" else 1]] if condition.startswith("E") else []
        )
        trials.append(result)
        new_trials += 1
        usage = result["observations"]["usage"]
        tokens += observed_tokens(usage)
        write_json(trial_dir / "result.json", result)
        write_json(output / "results/trials.json", trials)
        write_json(output / "results/summary.json", summarize(trials))
        print(
            json.dumps(
                {
                    "task": task_id,
                    "condition": condition,
                    "repeat": repeat,
                    "state": result["execution_state"],
                    "verdict": result["verdict"],
                }
            ),
            flush=True,
        )
        if result["execution_state"] != "completed":
            stop_reason = "client_" + result["execution_state"]
            break
    write_json(
        output / "results/status.json",
        {
            "expected_trials": len(plan["schedule"]),
            "recorded_trials": len(trials),
            "observed_tokens": tokens,
            "token_usage_complete": all(
                usage is not None
                and all(usage.get(key) is not None for key in ("input_tokens", "output_tokens"))
                for usage in (normalize_usage(t["observations"]["usage"]) for t in trials)
            ),
            "execution_state": "completed" if len(trials) == len(plan["schedule"]) else "stopped",
            "stop_reason": stop_reason,
        },
    )


def main():
    parser = argparse.ArgumentParser(description="Bounded report-audit skill pilot")
    parser.add_argument("mode", choices=("prepare", "execute"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model")
    parser.add_argument("--effort", default="xhigh")
    parser.add_argument("--protected-root", action="append", type=Path, default=[])
    parser.add_argument("--max-new-trials", type=int)
    parser.add_argument("--final-only", action="store_true")
    parser.add_argument("--campaign-seconds", type=int, default=5400)
    parser.add_argument("--token-limit", type=int, default=3000000)
    args = parser.parse_args()
    if (
        args.campaign_seconds <= 0
        or args.token_limit <= 0
        or (args.max_new_trials is not None and args.max_new_trials <= 0)
    ):
        parser.error("budgets and batch limits must be positive")
    if args.mode == "prepare":
        if not args.model or not args.protected_root:
            parser.error("prepare requires --model and --protected-root")
        prepare(
            args.output,
            args.model,
            args.effort,
            args.protected_root,
            args.final_only,
            args.campaign_seconds,
            args.token_limit,
        )
    else:
        execute(args.output, args.max_new_trials)


if __name__ == "__main__":
    main()
