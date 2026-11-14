import argparse
import difflib
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

from inferrsi.apps import skill_eval_engineering as artifact_check
from inferrsi.apps.skill_evaluation import export_bundle, run_trial, summarize
from inferrsi.benchmark import skill_eval_diagnostic as diagnostic
from inferrsi.benchmark import skill_eval_engineering as engineering
from inferrsi.harness import artifact_sandbox
from inferrsi.harness.skill_evaluation import CodexClient
from inferrsi.harness.usage import normalize_usage
from inferrsi.infra.artifacts import atomic_write_json, sha256, write_json
from inferrsi.knowledge_operations import KnowledgeReader, safe_path
from inferrsi.model.anthropic_agent import (
    FULL_CONTEXT_KIND,
    AnthropicAgentClient,
    validate_schema,
    validate_spec,
)
from inferrsi.model.anthropic_agent import SOURCE as AGENT_SOURCE
from inferrsi.model.request_guard import SOURCE as GUARD_SOURCE
from tools import skill_eval_artifact_guidance as artifact_guidance
from tools.skill_eval_pilot import PROMPT, SCHEMA, observed_tokens
from tools.skill_eval_snapshot import selected_snapshot
from tools.skill_eval_verifier import verify

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("baseline", "candidate")
GIT_READ = ("git", "--no-replace-objects", "--no-lazy-fetch")
CONTROLLERS = (GUARD_SOURCE, "tools/skill_eval_compare.py", "tools/skill_eval_pilot.py",
               "tools/skill_eval_artifact_guidance.py",
               "src/inferrsi/apps/skill_evaluation.py",
               "src/inferrsi/apps/skill_eval_engineering.py",
               "src/inferrsi/benchmark/skill_evaluation.py",
               "src/inferrsi/benchmark/skill_eval_diagnostic.py",
               "src/inferrsi/benchmark/skill_eval_engineering.py",
               "src/inferrsi/harness/skill_evaluation.py",
               "src/inferrsi/harness/artifact_sandbox.py", "tools/skill_eval_snapshot.py",
               "src/inferrsi/harness/local_process.py", "src/inferrsi/harness/atomic_json.py",
               "src/inferrsi/harness/input_binding.py", "src/inferrsi/infra/artifacts.py")
EVIDENCE = ("execution.json", "agent/stdout.log", "agent/stderr.log",
            "verification/result.json", "verification/stdout.log", "verification/stderr.log")
NUMBER = (int, float)
REPORT_INPUT = {
    "latencies_ms": [[NUMBER]],
    "workload": {"steps": int, "concurrency": int, "shape": [int]},
    "quality": str, "total_requests": int, "reported_requests": int, "failed_requests": int,
}
CASE_INPUT = {
    "id": str, "split": str, "mode": str, "provenance": str, "request": str,
    "contract": {
        "min_reduction_pct": NUMBER, "scope": str, "required_regimes": [str],
        "quality_required": bool, "all_requests_required": bool,
        "max_relative_spread_pct": NUMBER, "statistical_significance_required": bool,
        "verdict_definitions": {"PASS": str, "FAIL": str, "INCONCLUSIVE": str},
        "output_field_semantics": {"latency_reduction_pct": str, "claim_scope": str},
    },
    "evidence_scope": str, "baseline": REPORT_INPUT, "candidate": REPORT_INPUT,
    "evidence_keys": [str],
}
DEFAULT_AUDIT_CONTRACT = {
    "min_reduction_pct": None,
    "scope": ("fixed_workload",),
    "quality_required": (True,),
    "all_requests_required": (True,),
    "statistical_significance_required": (False,),
}
DEFAULT_AUDIT_EVIDENCE = {
    "contract", "evidence_scope", "baseline.latencies_ms", "candidate.latencies_ms",
    "baseline.workload", "candidate.workload", "candidate.quality",
    "candidate.failed_requests", "candidate.total_requests", "candidate.reported_requests",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate_input(value, shape, field="case") -> None:
    # Only the supported audit inputs may cross into the model-visible workspace.
    if isinstance(shape, dict):
        if not isinstance(value, dict) or value.keys() - shape.keys():
            raise ValueError(f"Unsupported input fields at {field}")
        for key, item in value.items():
            validate_input(item, shape[key], field + "." + key)
    elif isinstance(shape, list):
        if not isinstance(value, list):
            raise ValueError(f"Expected input list at {field}")
        for item in value:
            validate_input(item, shape[0], field + "[]")
    elif type(value) not in (shape if isinstance(shape, tuple) else (shape,)):
        raise ValueError(f"Unsupported input type at {field}")


def validate_audit_values(task: dict) -> None:
    for role in ROLES:
        if role not in task:
            continue
        report = task[role]
        if "quality" in report and report["quality"] not in ("passed", "failed", "missing"):
            raise ValueError(f"Unsupported quality at {role}.quality")
        if "latencies_ms" in report:
            groups = report["latencies_ms"]
            if not groups or any(not group for group in groups) or any(
                not math.isfinite(sample) or sample <= 0 for group in groups for sample in group
            ):
                raise ValueError(f"Expected nonempty groups of finite positive latencies at {role}")
        count_keys = ("total_requests", "reported_requests", "failed_requests")
        if any(name in report for name in count_keys):
            if not all(name in report for name in count_keys):
                raise ValueError(f"Incomplete request count fields at {role}")
            total, reported, failed = (report[name] for name in count_keys)
            if min(total, reported, failed) < 0 or reported > total or failed > total:
                raise ValueError(f"Inconsistent request counts at {role}")
            if total == 0 and report.get("latencies_ms"):
                raise ValueError(f"Latency observations require a positive request total at {role}")
            if total - reported > failed:
                raise ValueError(f"Unaccounted unreported requests at {role}")


def validate_default_audit(task: dict) -> None:
    # This boundary matches the existing grader, rather than promising new audit gates.
    required = {"mode", "contract", "evidence_scope", "baseline", "candidate", "evidence_keys"}
    if required - task.keys():
        raise ValueError("Default audit requires: " + ", ".join(sorted(required - task.keys())))
    if task["mode"] not in ("audit", "extract"):
        raise ValueError("Default audit supports audit and extract modes only")
    if task["evidence_scope"] not in ("http_fixed_workload", "kernel"):
        raise ValueError("Unsupported default audit evidence_scope")
    contract = task["contract"]
    if task["mode"] == "extract":
        if contract:
            raise ValueError("Default extract mode cannot enforce contract gates")
    else:
        if contract.keys() - DEFAULT_AUDIT_CONTRACT.keys():
            raise ValueError("Unsupported default audit contract fields: " + ", ".join(
                sorted(contract.keys() - DEFAULT_AUDIT_CONTRACT.keys())))
        if "min_reduction_pct" not in contract:
            raise ValueError("Default audit requires contract.min_reduction_pct")
        threshold = contract["min_reduction_pct"]
        if not math.isfinite(threshold) or not 0 <= threshold <= 100:
            raise ValueError("Default audit min_reduction_pct must be finite and within [0, 100]")
        for name, allowed in DEFAULT_AUDIT_CONTRACT.items():
            if name in contract and allowed is not None and contract[name] not in allowed:
                raise ValueError(f"Unsupported default audit contract value: {name}")
    for role in ROLES:
        report = task[role]
        if REPORT_INPUT.keys() - report.keys():
            raise ValueError(f"Default audit requires complete report fields at {role}")
        if sum(len(group) for group in report["latencies_ms"]) != report["reported_requests"]:
            raise ValueError(
                f"Default audit latency samples must match reported_requests at {role}")
        workload = report["workload"]
        if {"steps", "concurrency"} - workload.keys() or any(
            workload.get(name, 0) <= 0 for name in ("steps", "concurrency")
        ) or ("shape" in workload and (
            not workload["shape"] or any(dimension <= 0 for dimension in workload["shape"])
        )):
            raise ValueError(f"Default audit requires a positive workload at {role}")
    baseline = task["baseline"]
    if task["mode"] == "audit" and (
        baseline["quality"] != "passed" or baseline["failed_requests"] != 0
    ):
        raise ValueError("Default audit requires a passed baseline with no failed requests")
    evidence = {"contract", "evidence_scope"} | {
        f"{role}.{name}" for role in ROLES for name in REPORT_INPUT
    }
    if set(task["evidence_keys"]) - evidence:
        raise ValueError("Unsupported default audit evidence_keys")
    required_evidence = DEFAULT_AUDIT_EVIDENCE if task["mode"] == "audit" else {
        "baseline.latencies_ms", "candidate.latencies_ms"}
    if required_evidence - set(task["evidence_keys"]):
        raise ValueError("Missing default audit evidence_keys: " + ", ".join(
            sorted(required_evidence - set(task["evidence_keys"]))))


def validate(config: dict, tasks: list[dict], *, default_grader: bool = True) -> None:
    if set(config) - {"mode", "skill", "skill_root", "cases", "schema", "grader",
                      "snapshots", "model", "limits", "client", "pricing", "spend_limit",
                      "adapter", "execution_mode"}:
        raise ValueError("Unknown configuration field")
    adapter = config.get("adapter", "audit")
    if adapter not in ("audit", diagnostic.ADAPTER, engineering.ADAPTER):
        raise ValueError("Unknown experiment adapter")
    if adapter in (diagnostic.ADAPTER, engineering.ADAPTER):
        if config["mode"] != "update":
            raise ValueError("Diagnostic comparisons require paired old/new bundles (update mode)")
        if "grader" in config or "schema" in config:
            raise ValueError("Selected adapter owns its frozen schema and verifier")
    generation_only = config.get("execution_mode") == engineering.GENERATION_MODE
    if "execution_mode" in config and (not generation_only or adapter != engineering.ADAPTER):
        raise ValueError("Unsupported execution mode for selected adapter")
    if generation_only and (not isinstance(config.get("client"), dict)
                            or config["client"].get("kind") != FULL_CONTEXT_KIND):
        raise ValueError("Generation-only requires the bounded full-context provider")
    if adapter == engineering.ADAPTER and "client" in config and not generation_only:
        raise ValueError("Engineering artifacts require local execution, not read/submit provider")
    if config["mode"] not in ("addition", "update"):
        raise ValueError("mode must be addition or update")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", config["skill"]):
        raise ValueError("skill must be an unqualified checkout skill ID")
    safe_path(config["skill_root"])
    if set(config["snapshots"]) != set(ROLES):
        raise ValueError("snapshots must declare baseline and candidate")
    for role in ROLES:
        if set(config["snapshots"][role]) - {"git_revision", "bundle_revision"}:
            raise ValueError("Unknown snapshot field")
        if not re.fullmatch(r"[0-9a-f]{40}", config["snapshots"][role]["git_revision"]):
            raise ValueError("Both snapshots require full immutable Git commit SHAs")
    if config["mode"] == "addition" and "bundle_revision" in config["snapshots"]["baseline"]:
        raise ValueError("Addition baseline has no target bundle revision")
    if set(config["model"]) != {"name", "effort"}:
        raise ValueError("model supports name and effort only")
    if not all(isinstance(v, str) and v for v in config["model"].values()):
        raise ValueError("model name and effort are required")
    if "client" in config:
        validate_spec(config["model"], config["client"])
    elif "pricing" in config or "spend_limit" in config:
        raise ValueError("Provider pricing requires an explicit client")
    limits = config["limits"]
    if set(limits) != {"repeats", "max_task_calls", "timeout_seconds", "campaign_seconds",
                       "token_stop_new_trials"}:
        raise ValueError("Unknown or missing execution limit")
    for name in ("repeats", "max_task_calls", "timeout_seconds", "campaign_seconds",
                 "token_stop_new_trials"):
        if type(limits[name]) is not int or limits[name] <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if not tasks:
        raise ValueError("cases must be nonempty with unique IDs")
    for task in tasks:
        if adapter == engineering.ADAPTER:
            engineering.validate_task(task)
        elif adapter == diagnostic.ADAPTER:
            diagnostic.validate_task(task)
        else:
            validate_input(task, CASE_INPUT)
            validate_audit_values(task)
        missing = {"id", "split", "request"} - task.keys()
        if missing:
            raise ValueError("Missing required case fields: " + ", ".join(sorted(missing)))
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", task["id"]):
            raise ValueError("case IDs must be safe path components")
        if task["split"] not in ("development", "holdout", "discovery"):
            raise ValueError("Unknown case split")
        if default_grader and adapter == "audit":
            validate_default_audit(task)
    if len({task["id"] for task in tasks}) != len(tasks):
        raise ValueError("cases must be nonempty with unique IDs")
    if len(tasks) * limits["repeats"] * 2 > limits["max_task_calls"]:
        raise ValueError("schedule exceeds max_task_calls")


def build_schedule(tasks: list[dict], repeats: int) -> list[dict]:
    schedule = []
    for task in tasks:
        for repeat in range(1, repeats + 1):
            order = ROLES if (len(schedule) // 2) % 2 == 0 else tuple(reversed(ROLES))
            for role in order:
                schedule.append({"task_id": task["id"], "repeat": repeat, "condition": role,
                                 "split": task["split"]})
    return schedule


def snapshot(repo: Path, revision: str, skill_root: str, destination: Path, *,
             allow_empty: bool = False) -> Path:
    # Short temporary paths also accommodate deep libraries on Windows.
    kind = subprocess.check_output(
        [*GIT_READ, "cat-file", "-t", revision], cwd=repo, text=True,
    )
    if kind.strip() != "commit":
        raise ValueError("Snapshot revision must identify a Git commit")
    entries = subprocess.check_output(
        [*GIT_READ, "ls-tree", "-rz", revision, "--", skill_root], cwd=repo,
    ).split(b"\0")[:-1]
    if not entries:
        if allow_empty:
            return destination / skill_root
        raise ValueError("Snapshot skill_root contains no files")
    members = []
    for entry in entries:
        header, name = entry.split(b"\t", 1)
        mode, kind, digest = header.split()
        if mode == b"100755":
            raise ValueError("Executable snapshot entries are not supported")
        if mode != b"100644" or kind != b"blob":
            raise ValueError("Snapshot symlinks and submodules are not supported")
        members.append((safe_path(name.decode("utf-8")), digest))
    # Read raw Git blobs: archive/export attributes and checkout filters must not alter bytes.
    data = subprocess.run([*GIT_READ, "cat-file", "--batch"],
                          cwd=repo, check=True,
                          input=b"".join(digest + b"\n" for _, digest in members),
                          capture_output=True).stdout
    offset = 0
    for name, digest in members:
        end = data.index(b"\n", offset)
        if data[offset:end] == digest + b" missing":
            raise ValueError("Missing local snapshot blob: " + digest.decode("ascii"))
        actual, kind, size = data[offset:end].split()
        assert actual == digest and kind == b"blob"
        offset = end + 1
        size = int(size)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data[offset:offset + size])
        offset += size + 1
    return destination / skill_root


def require_git_version(version: str) -> None:
    parsed = re.match(r"git version (\d+)\.(\d+)(?:\.|$)", version.strip())
    if parsed is None or tuple(map(int, parsed.groups())) < (2, 45):
        raise ValueError("Snapshot preparation requires Git >= 2.45 for --no-lazy-fetch; found "
                         + version.strip())


def prepare(config_path: Path, repo: Path, output: Path) -> str:
    config = read_json(config_path)
    tasks = read_json(config_path.parent / config["cases"])
    is_diagnostic = config.get("adapter") == diagnostic.ADAPTER
    is_engineering = config.get("adapter") == engineering.ADAPTER
    grader = (Path(engineering.__file__) if is_engineering else
              Path(diagnostic.__file__) if is_diagnostic else
              config_path.parent / config["grader"] if "grader" in config else
              ROOT / "tools/skill_eval_verifier.py")
    validate(config, tasks, default_grader=(
        grader.read_bytes() == (ROOT / "tools/skill_eval_verifier.py").read_bytes()))
    require_git_version(subprocess.check_output(("git", "--version"), cwd=repo, text=True))
    generation_only = config.get("execution_mode") == engineering.GENERATION_MODE
    schema = (engineering.GENERATION_SCHEMA if generation_only else
              engineering.SCHEMA if is_engineering else
              diagnostic.SCHEMA if is_diagnostic else
              read_json(config_path.parent / config["schema"]) if "schema" in config else SCHEMA)
    if "client" in config:
        validate_schema(schema)
    output.mkdir(parents=True, exist_ok=False)
    for name in ("plan", "validator", "results"):
        (output / name).mkdir()
    write_json(output / "plan/tasks.json", tasks)
    write_json(output / "plan/schema.json", schema)
    shutil.copyfile(grader, output / "validator/verify.py")
    if is_engineering:
        (output / "plan/starter").mkdir()
        (output / "plan/starter/parser.py").write_bytes(engineering.STARTER.encode("utf-8"))
    arms = {}
    with tempfile.TemporaryDirectory(prefix="se-") as temporary:
        for role, short in zip(ROLES, ("b", "c")):
            selected = config["snapshots"][role]
            if is_diagnostic or is_engineering:
                closure = selected_snapshot(
                    repo, selected["git_revision"], config["skill_root"], config["skill"],
                    Path(temporary) / short, bundle_revision=selected.get("bundle_revision"))
                root = closure["root"]
                write_json(output / "plan/snapshots" / f"{role}.json",
                           {key: value for key, value in closure.items() if key != "root"})
            else:
                root = snapshot(repo, selected["git_revision"], config["skill_root"],
                                Path(temporary) / short,
                                allow_empty=role == "baseline" and config["mode"] == "addition")
            bundle = None
            if role == "candidate" or config["mode"] == "update":
                reader = KnowledgeReader(skill_root=root)
                reader.read("checkout:" + config["skill"], revision=selected.get("bundle_revision"))
                bundle = export_bundle(reader, "checkout:" + config["skill"],
                                       output / "plan/bundles" / role)
            arms[role] = {"git_revision": selected["git_revision"], "bundle": bundle}
    before = arms["baseline"]["bundle"]
    after = arms["candidate"]["bundle"]
    old = before["resources"] if before else {}
    new = after["resources"]
    changes = [{"path": path, "baseline": old.get(path), "candidate": new.get(path)}
               for path in sorted(old.keys() | new.keys()) if old.get(path) != new.get(path)]
    source_changes = subprocess.check_output(
        [*GIT_READ, "diff", "--name-status", arms["baseline"]["git_revision"],
         arms["candidate"]["git_revision"], "--"], cwd=repo, text=True,
    ).splitlines()
    files = {p.relative_to(output).as_posix(): sha256(p)
             for folder in (output / "plan", output / "validator")
             for p in sorted(folder.rglob("*")) if p.is_file()}
    plan = {
        "format_version": 1, "mode": config["mode"], "skill": config["skill"],
        "arms": arms, "model": config["model"], "limits": config["limits"],
        "schedule": build_schedule(tasks, config["limits"]["repeats"]),
        "bundle_changes": changes, "source_changes": source_changes,
        "exam": {"cases": files["plan/tasks.json"], "schema": files["plan/schema.json"],
                 "grader": files["validator/verify.py"]},
        "same_bundle": before is not None and before["revision"] == after["revision"],
        "prompt": (engineering.GENERATION_PROMPT if generation_only else
                   engineering.PROMPT if is_engineering else
                   diagnostic.PROMPT if is_diagnostic else PROMPT), "files": files,
        "controllers": {name: sha256(ROOT / name) for name in CONTROLLERS},
        "retry_policy": "none", "cost": None,
        "isolation": "Fresh sessions and workspaces; selected bundle only. "
        "CodexClient disables global skills, plugins, apps, memories and web. "
        "Controlled exposure is not OS read confinement.",
    }
    if is_diagnostic:
        plan["adapter"] = diagnostic.ADAPTER
    if is_engineering:
        plan["adapter"] = engineering.ADAPTER
        name = "src/inferrsi/benchmark/skill_eval_engineering.py"
        plan["controllers"][name] = sha256(ROOT / name)
        local_source = "tools/skill_eval_local.py"
        plan["controllers"][local_source] = sha256(ROOT / local_source)
        plan["isolation"] += (" Artifact replay requires bubblewrap namespaces and fails closed. "
                              "Public tests, not blind evaluation; reconstructed CPU fixture.")
    if "client" in config:
        client = AnthropicAgentClient(config["model"]["name"], config["client"],
                                      config["limits"]["timeout_seconds"],
                                      config.get("pricing"), config.get("spend_limit"))
        plan["client"] = client.frozen_identity()
        plan["controllers"][AGENT_SOURCE] = sha256(ROOT / AGENT_SOURCE)
        plan["max_api_requests"] = len(plan["schedule"]) * config["client"]["max_requests"]
        if "pricing" in config or "spend_limit" in config:
            plan["reservation"] = client.require_live_readiness(len(plan["schedule"]), now=0)
        plan["isolation"] = ("Fresh workspaces; read/submit tools expose selected files only. "
                             "No shell, network tool, external agent CLI or global config. "
                             "Provider worker and verifier use separate filtered environments.")
    if generation_only:
        plan["execution_mode"] = engineering.GENERATION_MODE
        plan["isolation"] = (
            "Generation only: the provider receives frozen task/starter and its assigned bundle. "
            "No shell, file-writing or test tools. "
            "A trusted controller materializes only parser.py; "
            "artifact code runs only in the independent bubblewrap verifier. "
            "This is not a workspace-agent or iterative tool-use experiment.")
    write_json(output / "plan/freeze.json", plan)
    return sha256(output / "plan/freeze.json")


def load_plan(output: Path) -> dict:
    plan = read_json(output / "plan/freeze.json")
    for name, digest in plan["controllers"].items():
        if sha256(ROOT / name) != digest:
            raise ValueError(f"Controller changed after freeze: {name}")
    for name, digest in plan["files"].items():
        if sha256(output / name) != digest:
            raise ValueError(f"Frozen input changed: {name}")
    actual = {p.relative_to(output).as_posix()
              for folder in (output / "plan", output / "validator")
              for p in folder.rglob("*") if p.is_file()}
    if actual != set(plan["files"]) | {"plan/freeze.json"}:
        raise ValueError("Frozen input file set changed")
    # The freeze hash covers every byte of freeze.json, so an edited copy can
    # always carry a matching hash. Re-derive what the frozen inputs determine
    # instead of trusting the fields that merely state it.
    limits = plan["limits"]
    if set(limits) != {"repeats", "max_task_calls", "timeout_seconds", "campaign_seconds",
                       "token_stop_new_trials"}:
        raise ValueError("Frozen execution limits changed shape")
    for name in limits:
        if type(limits[name]) is not int or limits[name] <= 0:
            raise ValueError(f"Frozen limit {name} must be a positive integer")
    tasks = read_json(output / "plan/tasks.json")
    if plan["schedule"] != build_schedule(tasks, limits["repeats"]):
        raise ValueError("Frozen schedule disagrees with the frozen tasks and repeats")
    if len(plan["schedule"]) > limits["max_task_calls"]:
        raise ValueError("Frozen schedule exceeds max_task_calls")
    if set(plan["model"]) != {"name", "effort"} or not all(
            isinstance(value, str) and value for value in plan["model"].values()):
        raise ValueError("Frozen model settings changed shape")
    for role in ROLES:
        # Bundle bytes are covered by `files` above; the arm only names the commit.
        arm = plan["arms"][role]
        if set(arm) != {"git_revision", "bundle"} or not re.fullmatch(
                r"[0-9a-f]{40}", arm["git_revision"]):
            raise ValueError("Frozen arm does not identify a Git commit")
    before = plan["arms"]["baseline"]["bundle"]
    after = plan["arms"]["candidate"]["bundle"]
    if plan["mode"] == "addition" and before is not None:
        raise ValueError("Addition baseline must not carry a bundle revision")
    if after is None:
        raise ValueError("Candidate arm requires an exported bundle")
    # The arm's bundle metadata states a revision and its resources; the exported
    # bytes are the in-tree authority for those resources. Compare them so an
    # edited arm cannot claim identical skill content that the export contradicts.
    for role in ROLES:
        bundle = plan["arms"][role]["bundle"]
        if bundle is None:
            continue
        export = output / "plan/bundles" / role
        actual = {path.relative_to(export).as_posix(): sha256(path)
                  for path in sorted(export.rglob("*")) if path.is_file()}
        if actual != bundle["resources"]:
            raise ValueError(f"Exported bundle bytes disagree with the frozen {role} arm")
    same_bundle = before is not None and before["revision"] == after["revision"]
    if plan["same_bundle"] != same_bundle:
        raise ValueError("Frozen identical-bundle flag disagrees with the frozen arms")
    if same_bundle and before["resources"] != after["resources"]:
        raise ValueError("Frozen identical-bundle claim disagrees with the exported bytes")
    frozen_exam = {"cases": "plan/tasks.json", "schema": "plan/schema.json",
                   "grader": "validator/verify.py"}
    for name, path in frozen_exam.items():
        if plan["exam"].get(name) != plan["files"].get(path):
            raise ValueError(f"Frozen exam hash disagrees with the frozen file list: {name}")
    return plan


def trial_directory(output: Path, entry: dict) -> Path:
    return output / "results" / f"{entry['task_id']}-{entry['condition']}-{entry['repeat']}"


def resolve_client(client):
    runtime = {"implementation": type(client).__module__ + "." + type(client).__qualname__,
               "executable": None, "executable_sha256": None, "version": None}
    if isinstance(client, AnthropicAgentClient):
        client.require_live_readiness()
        runtime.update(version=sys.version.split()[0], client_kind=client.spec["kind"],
                       executable=Path(sys.executable).name,
                       executable_sha256=sha256(Path(sys.executable)),
                       source_sha256=sha256(ROOT / AGENT_SOURCE))
    if isinstance(client, CodexClient):
        executable = shutil.which(client.executable)
        if executable is None:
            raise ValueError("Configured client executable is unavailable")
        executable = str(Path(executable).resolve())
        version = subprocess.check_output((executable, "--version"), text=True,
                                          timeout=min(10, client.timeout_seconds)).strip()
        if not version:
            raise ValueError("Client version output is empty")
        runtime.update(executable=Path(executable).name, executable_sha256=sha256(Path(executable)),
                       version=version)
        client = CodexClient(client.model, client.effort, client.timeout_seconds, executable)
    return client, runtime


def receipt_error(raw_record: bytes, receipt_path: Path, label: str) -> str | None:
    if not receipt_path.is_file():
        return f"missing_retained_{label}_receipt"
    try:
        receipt = read_json(receipt_path)
    except ValueError:
        return f"invalid_retained_{label}_receipt"
    if not isinstance(receipt, dict):
        return f"invalid_retained_{label}_receipt"
    if receipt.get("result_sha256") != hashlib.sha256(raw_record).hexdigest():
        return f"retained_{label}_record_hash_mismatch"
    return None


def provider_client(plan):
    identity = plan["client"]
    client = AnthropicAgentClient(plan["model"]["name"], identity["settings"],
                                  plan["limits"]["timeout_seconds"], identity.get("pricing"),
                                  identity.get("spend_limit"))
    if identity != client.frozen_identity():
        raise ValueError("Frozen provider identity no longer matches runtime")
    return client


def execute(output: Path, authorization: str, *, client=None,
            campaign_deadline: float | None = None,
            stop_on_unknown_usage: bool = True,
            require_request_authorization: bool = False) -> dict:
    if authorization != sha256(output / "plan/freeze.json"):
        raise ValueError("Execution requires authorization for this exact plan SHA-256")
    plan = load_plan(output)
    if any((output / "results").iterdir()):
        raise ValueError("Execution requires fresh results; prepare a new output directory")
    if require_request_authorization and "client" not in plan:
        raise ValueError("PR execution requires the guarded bounded provider")
    if "client" in plan:
        configured = provider_client(plan)
        if (require_request_authorization
                or plan.get("execution_mode") == engineering.GENERATION_MODE):
            configured = replace(configured, require_authorization=True)
        configured.require_live_readiness(len(plan["schedule"]))
        if client is not None:
            raise ValueError("A paid provider plan cannot substitute an injected client")
        configured.environment()  # Missing keys fail before start/trial evidence.
        client = configured
        stop_on_unknown_usage = True
    if plan.get("execution_mode") == engineering.GENERATION_MODE and "client" not in plan:
        raise ValueError("Generation-only requires the frozen bounded provider")
    if plan.get("adapter") == engineering.ADAPTER and client is None:
        raise ValueError("Engineering execution requires a verified confined local client; "
                         "the default danger-full-access Codex client is not qualified")
    if client is None:
        client = CodexClient(plan["model"]["name"], plan["model"]["effort"],
                             plan["limits"]["timeout_seconds"])
    client, runtime = resolve_client(client)
    write_json(output / "results/start.json",
               {"started_at": time.time(), "client_runtime": runtime})
    tasks = {task["id"]: task for task in read_json(output / "plan/tasks.json")}
    deadline = time.monotonic() + plan["limits"]["campaign_seconds"]
    if campaign_deadline is not None:
        deadline = min(deadline, campaign_deadline)
    tokens, completed, stop_reason = 0, 0, None
    budget_unknown = False
    for entry in plan["schedule"]:
        if time.monotonic() >= deadline:
            stop_reason = "time_budget"
            break
        if tokens >= plan["limits"]["token_stop_new_trials"]:
            stop_reason = "token_budget"
            break
        if budget_unknown and stop_on_unknown_usage:
            # An unknown budget cannot be shown to be within the authorized
            # limit, so no further trial is started. Checked before the next
            # trial, not after the last one, so a finished schedule stays finished.
            stop_reason = "unknown_usage"
            break
        task = tasks[entry["task_id"]]
        role = entry["condition"]
        bundle = plan["arms"][role]["bundle"]
        with tempfile.TemporaryDirectory(prefix="se-trial-") as temporary:
            workspace = Path(temporary)
            write_json(workspace / "task.json", task)
            is_engineering = plan.get("adapter") == engineering.ADAPTER
            if is_engineering:
                shutil.copyfile(output / "plan/starter/parser.py", workspace / "parser.py")
            prompt = plan["prompt"] + "\nTask operation: " + task["request"]
            if bundle is not None:
                folder = ".agents/skills" if task["split"] == "discovery" else "knowledge"
                shutil.copytree(output / "plan/bundles" / role, workspace / folder)
                if task["split"] != "discovery":
                    prompt += "\nUse the assigned skill at " + folder + "/" + bundle["entrypoint"]
            directory = trial_directory(output, entry)
            result = run_trial(client, task, role, entry["repeat"], workspace,
                               directory, output / "plan/schema.json",
                               output / "validator/verify.py", prompt, deadline,
                               timeout_seconds=plan["limits"]["timeout_seconds"],
                               engineering_artifact=is_engineering,
                               generation_only=(plan.get("execution_mode")
                                                == engineering.GENERATION_MODE))
            result["evidence_sha256"] = {name: sha256(directory / name) for name in EVIDENCE
                                         if (directory / name).is_file()}
            atomic_write_json(directory / "result.json", result)
            atomic_write_json(directory / "receipt.json",
                              {"result_sha256": sha256(directory / "result.json")})
        tokens += observed_tokens(result["observations"]["usage"])
        completed += 1
        if result["execution_state"] != "completed":
            stop_reason = "client_" + result["execution_state"]
            break
        usage = normalize_usage(result["observations"]["usage"])
        budget_unknown = usage is None or any(
            usage.get(key) is None for key in ("input_tokens", "output_tokens"))
    status = {"client_runtime": runtime,
              "recorded_trials": completed, "expected_trials": len(plan["schedule"]),
              "observed_tokens": tokens, "stop_reason": stop_reason,
              "execution_state": "completed" if completed == len(plan["schedule"])
              and stop_reason is None else "stopped"}
    atomic_write_json(output / "results/status.json", status)
    atomic_write_json(output / "results/status.receipt.json",
                      {"result_sha256": sha256(output / "results/status.json")})
    return report(output, replay_artifacts=(
        plan.get("execution_mode") != engineering.GENERATION_MODE))


def retained_trial(output: Path, entry: dict, expected_adapter=None, execution_mode=None) -> dict:
    directory = trial_directory(output, entry)
    path = directory / "result.json"
    if not path.is_file():
        interrupted = directory.is_dir()
        return {**entry, "verdict": "INCONCLUSIVE" if interrupted else None,
                "evaluation_error": "interrupted_attempt" if interrupted else None,
                "missing": not interrupted, "checks": None, "output": None,
                "evidence": {}, "elapsed_seconds": None, "observations": {"usage": None}}
    if expected_adapter == engineering.ADAPTER:
        # A retained tree is untrusted input too: reject special/symlink evidence
        # before legacy report readers open it. Producers must already be stopped.
        names = ("result.json", "receipt.json", "answer.json", "parser.py",
                 "author-execution.json", *EVIDENCE)
        try:
            for name in names:
                retained_path = directory / name
                if retained_path.exists() or retained_path.is_symlink():
                    artifact_sandbox.read_regular(retained_path, 8 * 1024 * 1024)
        except ValueError:
            return {**entry, "verdict": "INCONCLUSIVE", "evaluation_error": "unsafe_retained_file",
                    "missing": False, "checks": None, "output": None, "evidence": {},
                    "elapsed_seconds": None, "observations": {"usage": None}}
    raw_record = path.read_bytes()
    error = receipt_error(raw_record, directory / "receipt.json", "trial")
    answer = directory / "answer.json"
    data = answer.read_bytes() if answer.is_file() else None
    actual_hash = hashlib.sha256(data).hexdigest() if data is not None else None
    text = data.decode("utf-8", errors="backslashreplace") if data is not None else None
    if text is not None:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    evidence = {name: (directory / name).relative_to(output).as_posix()
                for name in ("result.json", "receipt.json", "answer.json", *EVIDENCE)
                if (directory / name).is_file()}
    if error:
        # Reject untrusted record bytes before parsing any of their fields.
        return {**entry, "verdict": "INCONCLUSIVE", "evaluation_error": error,
                "missing": False, "checks": None, "output": text, "evidence": evidence,
                "elapsed_seconds": None, "observations": {"usage": None},
                "output_sha256": None, "retained_output_sha256": actual_hash,
                "evidence_sha256": None, "retained_evidence_sha256": {}}
    record = json.loads(raw_record)
    if any(record[key] != entry[key] for key in ("task_id", "condition", "repeat", "split")):
        raise ValueError("Result identity disagrees with frozen schedule")
    # Select report fields; raw commands and private workspace paths stay in local artifacts.
    selected = {key: record[key] for key in ("task_id", "condition", "repeat", "split",
                "verdict", "checks", "elapsed_seconds", "observations", "inputs_unchanged")}
    # Legacy local imports used [] for unparsed logs, not an observed zero.
    unparsed_author_log = (record.get("execution_state") == "author_reported"
                           and record["observations"].get("completed_turns") is None)
    selected["observations"] = {
        "usage": normalize_usage(record["observations"]["usage"]),
        "tool_calls": len(record["observations"]["commands"])
        if (not unparsed_author_log
            and isinstance(record["observations"].get("commands"), list)) else None,
    }
    record_error = record.get("evaluation_error")
    # Artifact integrity failures take precedence over a replayable host error.
    error = None if expected_adapter == engineering.ADAPTER else record_error
    if (expected_adapter == engineering.ADAPTER
            and record.get("artifact_kind") != engineering.ADAPTER):
        error = error or "missing_retained_artifact_identity"
    if execution_mode == engineering.GENERATION_MODE:
        if record.get("execution_mode") != execution_mode:
            error = error or "missing_generation_mode_identity"
        elif not record["inputs_unchanged"] or record.get("execution_state") != "completed":
            error = "invalid_generation_execution"
        else:
            selected["execution_mode"] = execution_mode
            selected["artifact_verdict"] = None
    if record.get("execution_state") == "author_reported":
        selected["provenance"] = record.get("provenance")
        selected["author_reported_execution"] = True
        selected["artifact_verdict"] = None  # Re-derived below, never trusted from a receipt.
    if record["observations"].get("client_kind"):
        selected["observations"].update({
            key: record["observations"].get(key) for key in
            ("client_kind", "provider_model", "api_requests", "usage_complete", "observed_usage")})
    if data is None:
        error = error or "missing_retained_output"
    elif actual_hash != record["output_sha256"]:
        error = error or "retained_output_hash_mismatch"
    digests = record.get("evidence_sha256")
    retained = {}
    if digests is None:
        error = error or "missing_retained_evidence_hashes"
    else:
        required_evidence = (*EVIDENCE, "author-execution.json") if (
            record["execution_state"] == "author_reported") else EVIDENCE
        for name in required_evidence:
            if name not in digests:
                if record["verdict"] in ("PASS", "FAIL"):
                    error = error or "missing_retained_evidence_hashes"
                continue
            artifact = directory / name
            retained[name] = sha256(artifact) if artifact.is_file() else None
            if retained[name] is None:
                error = error or "missing_retained_evidence"
            elif retained[name] != digests[name]:
                error = error or "retained_evidence_hash_mismatch"
            elif name == "author-execution.json":
                evidence[name] = artifact.relative_to(output).as_posix()
    if record.get("artifact_kind") == engineering.ADAPTER and record.get("artifact_sha256") is None:
        error = error or "missing_retained_artifact"
    if record.get("artifact_sha256") is not None:
        try:
            artifact_answer = json.loads(data) if data is not None else None
        except ValueError:
            artifact_answer = None
        artifact = directory / "parser.py"
        if artifact.is_symlink() or not artifact.is_file():
            error = error or "missing_retained_artifact"
        elif sha256(artifact) != record["artifact_sha256"]:
            error = error or "retained_artifact_hash_mismatch"
        elif (not isinstance(artifact_answer, dict)
              or artifact_answer.get("artifact_source") != artifact.read_bytes().decode("utf-8")):
            error = error or "retained_artifact_disagrees_with_answer"
        else:
            evidence["parser.py"] = artifact.relative_to(output).as_posix()
    error = error or record_error
    if (error or record["execution_state"] not in ("completed", "author_reported")
            or not record["inputs_unchanged"]):
        selected["verdict"] = "INCONCLUSIVE"
    return {**selected, "evaluation_error": error, "missing": False,
            "output": text,
            "output_sha256": record["output_sha256"], "retained_output_sha256": actual_hash,
            "evidence_sha256": digests, "retained_evidence_sha256": retained,
            "evidence": evidence}


def condition_summary(trials: list[dict]) -> dict:
    groups = summarize([t for t in trials if not t["missing"]])["conditions"]
    for role in ROLES:
        selected = [t for t in trials if t["condition"] == role]
        group = groups.setdefault(role, {"trials": 0, "verdicts": {}, "evaluation_errors": 0,
                                         "elapsed_seconds": None, "cost": None})
        group["expected_trials"] = len(selected)
        group["missing_trials"] = sum(t["missing"] for t in selected)
        for metric in ("input_tokens", "output_tokens"):
            usage = [normalize_usage(t["observations"]["usage"]) for t in selected]
            group[metric] = sum(u[metric] for u in usage) if all(
                u is not None and u.get(metric) is not None for u in usage
            ) and usage else None
    return groups


def read_report(output: Path, *, replay_artifacts: bool = True) -> dict:
    plan = load_plan(output)
    trials = [retained_trial(output, entry, plan.get("adapter"), plan.get("execution_mode"))
              for entry in plan["schedule"]]
    # A receipt is an unkeyed hash: a rewritten tree can carry a consistent one.
    # Re-grading retained answers with trusted code is what makes a PASS/FAIL
    # verdict independent of the artifact copy the agent could reach. This runs
    # before pairs are formed so an unverifiable grade cannot become an outcome.
    if replay_artifacts:
        verify_retained_grader(plan, [t for t in trials if not t["missing"]],
                               {task["id"]: task for task in read_json(output / "plan/tasks.json")})
    index = {(t["task_id"], t["repeat"], t["condition"]): t for t in trials}
    pairs = []
    for entry in plan["schedule"]:
        if entry["condition"] != "baseline" or entry["split"] == "discovery":
            continue
        arms = {role: index[entry["task_id"], entry["repeat"], role] for role in ROLES}
        before, after = (arms[role]["verdict"] for role in ROLES)
        if any(arm["missing"] for arm in arms.values()):
            outcome = "missing_pair"
        elif "INCONCLUSIVE" in (before, after):
            outcome = "inconclusive"
        else:
            outcome = {("FAIL", "PASS"): "fix", ("PASS", "FAIL"): "regression",
                       ("PASS", "PASS"): "unchanged_correct",
                       ("FAIL", "FAIL"): "unchanged_incorrect"}[before, after]
        manual = any(arm.get("author_reported_execution") for arm in arms.values())
        if manual:
            outcome = {"fix": "artifact_improved", "regression": "artifact_regressed",
                       "unchanged_correct": "artifacts_both_pass",
                       "unchanged_incorrect": "artifacts_both_fail"}.get(outcome, outcome)
        pairs.append({"comparability": "unverified_author_reported" if manual else "controller_run",
                      "case_id": entry["task_id"], "repeat": entry["repeat"],
                      "outcome": outcome, **arms})
    if plan.get("execution_mode") == engineering.GENERATION_MODE:
        for pair in pairs:
            replayed = tuple(pair[role].get("artifact_verdict") for role in ROLES)
            pair["comparability"] = "generation_only_provider"
            pair["replayed_artifact_outcome"] = {
                ("FAIL", "PASS"): "artifact_improved", ("PASS", "FAIL"): "artifact_regressed",
                ("PASS", "PASS"): "artifacts_both_pass", ("FAIL", "FAIL"): "artifacts_both_fail",
            }.get(replayed, "inconclusive")
    discovery = [t for t in trials if t["split"] == "discovery"]
    status = {"execution_state": "not_completed"}
    status_path = output / "results/status.json"
    status_receipt = output / "results/status.receipt.json"
    if status_path.is_file() or status_receipt.is_file():
        raw_status = status_path.read_bytes() if status_path.is_file() else None
        error = "missing_retained_status_record" if raw_status is None else (
            receipt_error(raw_status, status_receipt, "status"))
        status = {"execution_state": "unverified", "evaluation_error": error,
                  "expected_trials": len(plan["schedule"]), "recorded_trials": None,
                  "observed_tokens": None, "stop_reason": None, "client_runtime": None}
        if error is None:
            status = json.loads(raw_status)
    result = {
        "mode": plan["mode"], "skill": plan["skill"],
        "plan_sha256": sha256(output / "plan/freeze.json"),
        "arms": plan["arms"], "model": plan["model"], "limits": plan["limits"],
        "exam": plan["exam"], "same_bundle": plan["same_bundle"],
        "bundle_changes": plan["bundle_changes"], "source_changes": plan["source_changes"],
        "conditions": condition_summary([t for t in trials if t["split"] != "discovery"]),
        "pairs": pairs,
        "counts": {name: sum(p["outcome"] == name for p in pairs) for name in (
            "fix", "regression", "unchanged_correct", "unchanged_incorrect", "missing_pair",
            "inconclusive", *( ("artifact_improved", "artifact_regressed",
                               "artifacts_both_pass", "artifacts_both_fail")
                             if plan.get("adapter") == engineering.ADAPTER else ()))},
        "discovery": discovery, "discovery_conditions": condition_summary(discovery),
        "status": status,
        "claim": "Pipeline results only; fixed-output offline clients do not establish "
        "skill efficacy. Discovery is separate. Unknown usage and cost remain unknown.",
    }
    if plan.get("adapter") == engineering.ADAPTER:
        result["adapter"] = engineering.ADAPTER
        if any(trial.get("author_reported_execution") for trial in trials):
            captured = sum(bool(trial.get("author_reported_execution")) for trial in trials)
            result["status"] = {"execution_state": "author_reported_unverified",
                                "artifact_capture_state": "complete" if captured == len(trials)
                                else "partial", "recorded_trials": captured,
                                "expected_trials": len(trials)}
        result["claim"] = (
            "Reconstructed CPU parser artifact checks only. Author-run provenance, environment, "
            "usage and costs are self-reported, not independently authenticated. Public tests "
            "may be overfit. Paired artifact differences do not prove skill causality, original "
            "engine behavior, hardware efficacy or whole-PR acceptance. Reviewer decides merge.")
    if plan.get("execution_mode") == engineering.GENERATION_MODE:
        result["execution_mode"] = engineering.GENERATION_MODE
        result["claim"] = (
            "Generation-only provider trial: no model shell, file-writing or test tools. "
            "The trusted controller materializes parser.py, and independent replay checks "
            "the retained artifact. This does not measure iterative workspace-agent performance. "
            "Public reconstructed CPU tests may be overfit; artifact differences alone do not "
            "establish skill causality, hardware efficacy or whole-PR acceptance.")
    if "client" in plan:
        result["client"] = plan["client"]
        result["max_api_requests"] = plan["max_api_requests"]
        result["reservation"] = plan.get("reservation")
    if plan.get("adapter") == engineering.ADAPTER:
        result["guidance"] = artifact_guidance.comparison(result)
    return result


def verify_retained_grader(plan: dict, trials: list[dict], tasks: dict) -> None:
    """Re-derive every positive verdict with trusted code, not the frozen copy.

    `validator/verify.py` lives beside the agent's workspace and the agent runs
    first, so those bytes cannot establish a grade on their own. When the frozen
    exam is the repository verifier, its verdict is computed here again from the
    retained answer. A custom grader has no in-tree authority to re-derive from;
    that exam is reported as unverified rather than silently trusted.
    """
    trusted_verifier = {
        sha256(ROOT / "tools/skill_eval_verifier.py"): verify,
        sha256(Path(diagnostic.__file__)): diagnostic.verify,
        sha256(Path(engineering.__file__)): artifact_check.verify,
    }.get(plan["exam"]["grader"])
    if trusted_verifier is None:
        for trial in trials:
            if trial["verdict"] in ("PASS", "FAIL"):
                # No in-tree authority can re-derive this grade, and the retained
                # record is writable by the graded agent, so the stated outcome is
                # not evidence. Keep the raw verdict visible under its own field,
                # but do not let it count as a fix, regression or unchanged pair.
                trial["stated_verdict"] = trial["verdict"]
                trial["verdict"] = "INCONCLUSIVE"
                trial["grader_trust"] = "unverified"
                trial["evaluation_error"] = "unverified_custom_grader"
        return
    for trial in trials:
        manual_artifact = (trusted_verifier is artifact_check.verify
                           and trial.get("author_reported_execution")
                           and trial.get("evaluation_error") in (
                               None, "reported_agent_failure", "artifact_sandbox_unavailable",
                               "reported_run_exceeded_frozen_timeout"))
        generated_artifact = (
            trusted_verifier is artifact_check.verify
            and plan.get("execution_mode") == engineering.GENERATION_MODE
            and trial.get("execution_mode") == engineering.GENERATION_MODE
            and trial.get("evaluation_error") in (None, "artifact_sandbox_unavailable",
                                                    "artifact_verification_deferred"))
        replay_artifact = manual_artifact or generated_artifact
        if replay_artifact:
            trial["artifact_verdict"] = None
            trial.pop("artifact_checks", None)
            trial.pop("artifact_replay_error", None)
        if trial["verdict"] not in ("PASS", "FAIL") and not replay_artifact:
            continue
        try:
            checks = trusted_verifier(tasks[trial["task_id"]], json.loads(trial["output"]))
        except artifact_sandbox.SandboxUnavailable:
            trial["verdict"] = "INCONCLUSIVE"
            trial["grader_trust"] = "unverified"
            if generated_artifact:
                trial["artifact_replay_error"] = "artifact_sandbox_unavailable"
            else:
                trial["evaluation_error"] = "artifact_sandbox_unavailable"
            continue
        replay_after_blocked_capture = (
            replay_artifact and trial["checks"] is None
            and trial["evaluation_error"] in ("artifact_sandbox_unavailable",
                                               "artifact_verification_deferred"))
        if not replay_after_blocked_capture and (
                checks != trial["checks"] or (trial["verdict"] in ("PASS", "FAIL")
                and checks["passed"] != (trial["verdict"] == "PASS"))):
            raise ValueError("Untrusted grading disagrees with the trusted grader")
        if replay_artifact:
            # Report can recheck a portable artifact on a capable reviewer host,
            # without rewriting capture failure or upgrading pair comparability.
            trial["artifact_checks"] = checks
            trial["artifact_verdict"] = "PASS" if checks["passed"] else "FAIL"
        trial["grader_trust"] = "reverified"


def report(output: Path, *, replay_artifacts: bool = True) -> dict:
    result = read_report(output, replay_artifacts=replay_artifacts)
    write_json(output / "report.json", result)
    (output / "report.md").write_text(markdown(result), encoding="utf-8")
    return result


def markdown(result: dict) -> str:
    lines = ["# Skill comparison", "", result["claim"], "",
             f"Mode: **{result['mode']}** · Skill: `{result['skill']}`", "",
             f"Frozen plan: `{result['plan_sha256']}`", "",
             "| Role | Git revision | Bundle revision |", "| --- | --- | --- |"]
    for role, arm in result["arms"].items():
        bundle = arm["bundle"]
        lines.append(f"| {role} | `{arm['git_revision']}` | "
                     + (f"`{bundle['revision']}`" if bundle else "absent") + " |")
    if "client" in result:
        lines += ["", "Client/provider identity: `"
                  + json.dumps(result["client"], sort_keys=True) + "`",
                  "Maximum API requests across both arms: "
                  + str(result["max_api_requests"]) + "."]
    lines += ["", "Shared model/settings: `" + json.dumps(result["model"], sort_keys=True) + "`",
              "", "Execution limits: `" + json.dumps(result["limits"], sort_keys=True) + "`",
              "", "Execution status/client runtime: `"
              + json.dumps(result["status"], sort_keys=True) + "`",
              "", "Frozen exam hashes: `" + json.dumps(result["exam"], sort_keys=True) + "`",
              "", f"Identical bundle: {result['same_bundle']}", "",
              "Counts: `" + json.dumps(result["counts"], sort_keys=True) + "`", "",
              "Usage (null means unknown): `" + json.dumps(result["conditions"], sort_keys=True)
              + "`", "", "| Case | Repeat | Baseline | Candidate | Outcome |",
              "| --- | --- | --- | --- | --- |"]
    for pair in result["pairs"]:
        lines.append(f"| {pair['case_id']} | {pair['repeat']} | "
                     f"{pair['baseline']['verdict'] or 'missing'} | "
                     f"{pair['candidate']['verdict'] or 'missing'} | {pair['outcome']} |")
    lines += ["", "## Retained outputs and grading evidence", ""]
    if result["discovery"]:
        lines += ["Discovery is excluded from the paired counts and main condition totals.", "",
                  "Discovery totals: `" + json.dumps(result["discovery_conditions"], sort_keys=True)
                  + "`", ""]
    for trial in [p[role] for p in result["pairs"] for role in ROLES] + result["discovery"]:
        output_text = trial["output"] if trial["output"] is not None else "(unavailable)"
        longest = max((len(run) for run in re.findall(r"`+", output_text)), default=0)
        fence = "`" * max(4, longest + 1)
        lines += [f"### {trial['task_id']} / {trial['repeat']} / {trial['condition']}", "",
                  f"Verdict: {trial['verdict'] or 'missing'}; error: "
                  f"{trial['evaluation_error'] or 'none'}; split: {trial['split']}", ""]
        if trial.get("provenance"):
            lines += ["Provenance: " + trial["provenance"],
                      "Independent artifact outcome: " + str(trial.get("artifact_verdict")), ""]
        tool_calls = trial["observations"].get("tool_calls")
        lines += ["Observed command count: "
                  + ("unknown" if tool_calls is None else str(tool_calls)), ""]
        lines += [f"[{name}]({path})" for name, path in trial["evidence"].items()]
        lines += ["", "Grading assertions:", "", "```json",
                  json.dumps(trial["checks"], ensure_ascii=False, sort_keys=True), "```", "",
                  "Output (invalid UTF-8 bytes are shown as backslash escapes):", "",
                  fence + "text", output_text, fence, ""]
    lines += ["## Snapshot changes", "", "### Exported resources and dependencies", "",
              "```json", json.dumps(result["bundle_changes"], indent=2), "```", "",
              "### Git source changes", "", "The same frozen exam is used by both arms. "
              "Source changes below are surfaced, not substituted for that exam.", "", "```text",
              "\n".join(result["source_changes"]) or "(none)", "```", ""]
    rendered = "\n".join(lines)
    if result.get("adapter") != engineering.ADAPTER:
        return rendered
    summary = ["# Local engineering artifact evidence", "",
               artifact_guidance.markdown(result.get("guidance")
                                          or artifact_guidance.comparison(result)),
               result["claim"], "",
               "**Merge implication:** artifact checks inform review; they do not authorize merge. "
               "Verify current head, source fidelity, author-run controls and remaining PR scope.",
               "",
               "| Task | Baseline artifact | Candidate artifact | Observed difference |",
               "| --- | --- | --- | --- |"]
    for pair in result["pairs"]:
        outcomes = [pair[role].get("artifact_verdict") or pair[role]["verdict"] or "missing"
                    for role in ROLES]
        summary.append(f"| {pair['case_id']} | {outcomes[0]} | {outcomes[1]} | "
                       f"{pair.get('replayed_artifact_outcome', pair['outcome'])} |")
    summary += ["", "## Failed checks and coverage", "",
                "Public reconstructed response-parser checks only; no original server, GPU, "
                "retrieval quality or whole-corpus coverage."]
    for pair in result["pairs"]:
        for role in ROLES:
            trial = pair[role]
            checks = trial.get("artifact_checks") or trial.get("checks") or {}
            failed = [name for name, passed in checks.get("assertions", {}).items() if not passed]
            summary.append(f"- {pair['case_id']} / {role}: "
                           + (", ".join(failed) if failed else "no failed check retained")
                           + "; limitation/error: " + str(trial.get("evaluation_error") or "none")
                           + "; comparability: " + pair["comparability"] + ".")
        sources = []
        for role in ROLES:
            try:
                answer = json.loads(pair[role].get("output") or "null")
            except ValueError:
                answer = None
            sources.append(answer.get("artifact_source") if isinstance(answer, dict) else None)
        if all(isinstance(source, str) for source in sources):
            delta = "".join(difflib.unified_diff(sources[0].splitlines(keepends=True),
                                                sources[1].splitlines(keepends=True),
                                                fromfile="baseline/parser.py",
                                                tofile="candidate/parser.py"))
            fence = "`" * max(4, 1 + max((len(run) for run in re.findall(r"`+", delta)), default=0))
            summary += ["", "### Retained artifact difference: " + pair["case_id"], "",
                        fence + "diff", delta or "(identical artifact bytes)", fence]
    summary += ["", "<details><summary>Revisions, settings, outputs and audit evidence</summary>",
                "", rendered, "", "</details>", ""]
    return "\n".join(summary)


def main():
    parser = argparse.ArgumentParser(description="Configured skill addition/update comparison")
    parser.add_argument("mode", choices=("prepare", "execute", "report"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--authorize", help="Explicit execution authorization: frozen plan SHA-256")
    handoff = parser.add_mutually_exclusive_group()
    handoff.add_argument("--stage-local", type=Path, help="Export one author-run workspace")
    handoff.add_argument("--capture-local", type=Path, help="Capture one author-run workspace")
    parser.add_argument("--submission", type=Path, help="Author logs and execution.json directory")
    parser.add_argument("--task-id")
    parser.add_argument("--condition", choices=ROLES)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--current-head",
                        help="Reviewer-checked current PR head; not fetched by CLI")
    args = parser.parse_args()
    output = args.output.resolve()
    if args.mode == "prepare":
        if args.config is None or args.repo is None:
            parser.error("prepare requires --config and --repo")
        digest = prepare(args.config.resolve(), args.repo.resolve(), output)
        print(json.dumps({"plan_sha256": digest}))
    elif args.mode == "execute":
        if args.authorize is None:
            parser.error("execute requires --authorize <frozen-plan-sha256>")
        if args.stage_local or args.capture_local:
            from tools import skill_eval_local

            if not args.task_id or not args.condition or not args.current_head:
                parser.error("Local handoff requires --task-id, --condition, --current-head")
            common = (output, args.authorize, args.task_id, args.condition, args.repeat)
            if args.stage_local:
                result = skill_eval_local.stage(*common, args.stage_local.resolve(),
                                                args.current_head)
            else:
                if args.submission is None:
                    parser.error("Capture requires --submission with author logs")
                result = skill_eval_local.capture(*common, args.capture_local.resolve(),
                                                  args.current_head, args.submission.resolve())
            print(json.dumps(result))
        else:
            execute(output, args.authorize)
    else:
        report(output)


if __name__ == "__main__":
    main()
