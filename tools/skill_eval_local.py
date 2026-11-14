"""Author-run local artifact handoff; no agent launch or CI result import.

Stage exports one frozen arm. Capture records author-supplied execution evidence
and independently checks the actual artifact. It does not authenticate who ran an
agent, its access boundaries, its costs, or the completeness of the supplied log.
"""
import hashlib
import json
import math
import os
import shutil
import stat
from pathlib import Path

from inferrsi.apps import skill_eval_engineering as artifact_check
from inferrsi.benchmark import skill_eval_engineering as engineering
from inferrsi.harness import artifact_sandbox
from inferrsi.harness.usage import normalize_usage
from inferrsi.infra.artifacts import sha256, write_json


def _selection(output, authorization, task_id, condition, repeat, current_head):
    from tools import skill_eval_compare as compare

    if authorization != sha256(output / "plan/freeze.json"):
        raise ValueError("Local handoff requires the exact frozen plan SHA-256")
    plan = compare.load_plan(output)
    if plan.get("adapter") != engineering.ADAPTER:
        raise ValueError("Local artifact handoff is only for the engineering adapter")
    if current_head != plan["arms"]["candidate"]["git_revision"]:
        raise ValueError("Current PR head differs from the frozen candidate; revalidate first")
    selected = [entry for entry in plan["schedule"] if entry["task_id"] == task_id
                and entry["condition"] == condition and entry["repeat"] == repeat]
    if len(selected) != 1:
        raise ValueError("Trial is not in the frozen schedule")
    task = next(task for task in compare.read_json(output / "plan/tasks.json")
                if task["id"] == task_id)
    return plan, selected[0], task, compare.trial_directory(output, selected[0])


def _read_regular(path, bound=8 * 1024 * 1024):
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Local evidence must not traverse symlinks")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise ValueError("Local evidence is missing or cannot be safely opened") from error
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > bound:
            raise ValueError("Local evidence must be a bounded regular file")
        data = stream.read(bound + 1)
    if len(data) > bound:
        raise ValueError("Local evidence grew beyond its bound")
    return data


def _hashes(workspace):
    result = {}
    for path in workspace.rglob("*"):
        if path.is_symlink():
            raise ValueError("Local workspace must not contain symlinks")
        if path.is_dir():
            continue
        name = path.relative_to(workspace).as_posix()
        if name != "parser.py":
            result[name] = hashlib.sha256(_read_regular(path)).hexdigest()
    return result


def _prompt(plan, task, condition):
    prompt = plan["prompt"] + "\nTask operation: " + task["request"]
    bundle = plan["arms"][condition]["bundle"]
    if bundle:
        prompt += "\nUse assigned skill at knowledge/" + bundle["entrypoint"]
    return prompt


def _expected_inputs(plan, task, condition):
    def digest(text):
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    inputs = {"task.json": digest(json.dumps(task, ensure_ascii=False, indent=2,
                                           allow_nan=False) + "\n"),
              "INSTRUCTIONS.txt": digest(_prompt(plan, task, condition))}
    bundle = plan["arms"][condition]["bundle"]
    if bundle:
        inputs.update({"knowledge/" + name: value for name, value in bundle["resources"].items()})
    return inputs


def stage(output: Path, authorization: str, task_id: str, condition: str, repeat: int,
          workspace: Path, current_head: str):
    plan, entry, task, directory = _selection(
        output, authorization, task_id, condition, repeat, current_head)
    if directory.exists() or workspace.exists():
        raise ValueError("Stage requires fresh trial and workspace; never overwrite an attempt")
    # Keep writable work separate from retained evidence/validator and the other arm.
    if output.resolve() in workspace.resolve().parents:
        raise ValueError("Stage workspace outside the evidence tree")
    workspace.mkdir(parents=True)
    # Frozen inputs are LF bytes on every host; never apply platform translation.
    (workspace / "task.json").write_bytes(
        (json.dumps(task, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    shutil.copyfile(output / "plan/starter/parser.py", workspace / "parser.py")
    bundle = plan["arms"][condition]["bundle"]
    prompt = _prompt(plan, task, condition)
    if bundle:
        shutil.copytree(output / "plan/bundles" / condition, workspace / "knowledge")
    (workspace / "INSTRUCTIONS.txt").write_bytes(prompt.encode("utf-8"))
    directory.mkdir(parents=True)
    write_json(directory / "local-stage.json", {
        "plan_sha256": authorization, "entry": entry, "input_hashes": _hashes(workspace),
        "provenance": "author-run; execution not observed by controller",
    })
    return {"entry": entry, "plan_sha256": authorization,
            "next": "Run approved local agent; only parser.py is writable. Keep logs separately. "
                    "Capture does not authorize or pay for that run."}


def capture(output: Path, authorization: str, task_id: str, condition: str, repeat: int,
            workspace: Path, current_head: str, submission: Path):
    plan, entry, task, directory = _selection(
        output, authorization, task_id, condition, repeat, current_head)
    stage_record = json.loads(_read_regular(directory / "local-stage.json"))
    if stage_record["plan_sha256"] != authorization or stage_record["entry"] != entry:
        raise ValueError("Stage receipt does not match the frozen trial")
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError("Retained evidence directory must not traverse symlinks")
    if {path.name for path in directory.iterdir()} != {"local-stage.json"}:
        # Includes dangling symlinks: .exists() alone would let writes escape.
        raise ValueError("Capture already started or reserved targets exist; "
                         "inspect, never overwrite")
    meta_path = submission / "execution.json"
    required = (meta_path, submission / "stdout.log", submission / "stderr.log")
    if any(path.is_symlink() or not path.is_file() for path in required):
        raise ValueError("Supply execution.json, stdout.log and stderr.log as regular files")
    if any(path.stat().st_size > 8 * 1024 * 1024 for path in required):
        raise ValueError("Local evidence exceeds the 8 MiB per-file bound")
    submitted = {path.name: _read_regular(path) for path in required}
    metadata = json.loads(submitted["execution.json"])
    if set(metadata) != {"model", "environment", "access", "resources", "usage",
                         "elapsed_seconds", "agent_exit_code", "summary"}:
        raise ValueError("Local execution evidence has unknown or missing fields")
    if metadata["model"] != plan["model"]:
        raise ValueError("Author-stated model differs from frozen model")
    if any(not isinstance(metadata[key], str) or not metadata[key].strip()
           for key in ("environment", "access", "resources", "summary")):
        raise ValueError("Environment, access, resources and summary must be explicit")
    elapsed = metadata["elapsed_seconds"]
    if (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0
            or type(metadata["agent_exit_code"]) is not int):
        raise ValueError("Invalid author-stated execution outcome")
    error = None
    try:
        expected = _expected_inputs(plan, task, condition)
        unchanged = (_hashes(workspace) == expected
                     and stage_record["input_hashes"] == expected)
    except ValueError:
        unchanged = False
    if not unchanged:
        error = "inputs_changed"
    artifact = workspace / "parser.py"
    source = None
    artifact_bytes = None
    if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size > 65536:
        error = error or "missing_or_invalid_artifact"
    else:
        try:
            artifact_bytes = _read_regular(artifact, 65536)
            if len(artifact_bytes) > 65536:
                raise UnicodeError("artifact grew beyond bound")
            source = artifact_bytes.decode("utf-8")
        except (UnicodeError, ValueError):
            error = error or "invalid_artifact_encoding"
    # Evidence is explicitly reported, not an invented controller-run agent process.
    execution = {"status": "reported", "agent_exit_code": metadata["agent_exit_code"],
                 "elapsed_seconds": elapsed, "provenance": "author-supplied"}
    write_json(directory / "execution.json", execution)
    write_json(directory / "author-execution.json", metadata)
    (directory / "agent").mkdir()
    for name in ("stdout.log", "stderr.log"):
        (directory / "agent" / name).write_bytes(submitted[name])
    answer = {"artifact": "parser.py", "summary": metadata["summary"],
              "artifact_source": source}
    write_json(directory / "answer.json", answer)
    if source is not None:
        (directory / "parser.py").write_bytes(artifact_bytes)
    checks = None
    if error is None:
        try:
            checks = artifact_check.verify(task, answer)
        except artifact_sandbox.SandboxUnavailable:
            error = "artifact_sandbox_unavailable"
    (directory / "verification").mkdir()
    for name in ("stdout.log", "stderr.log"):
        (directory / "verification" / name).write_text("", encoding="utf-8")
    if checks is not None:
        write_json(directory / "verification/result.json", checks)
    artifact_verdict = None if checks is None else "PASS" if checks["passed"] else "FAIL"
    if metadata["agent_exit_code"] != 0:
        error = error or "reported_agent_failure"
    if elapsed > plan["limits"]["timeout_seconds"]:
        error = error or "reported_run_exceeded_frozen_timeout"
    verdict = "INCONCLUSIVE" if error else artifact_verdict
    from tools import skill_eval_compare as compare

    record = {**entry, "verdict": verdict, "checks": checks, "elapsed_seconds": elapsed,
              # Raw author logs are retained, not parsed into command observations.
              "observations": {"usage": normalize_usage(metadata["usage"]), "commands": None,
                               "completed_turns": None, "errors": []},
              "execution_state": "author_reported", "inputs_unchanged": unchanged,
              "evaluation_error": error, "input_hashes": stage_record["input_hashes"],
              "output_sha256": sha256(directory / "answer.json"),
              "artifact_kind": engineering.ADAPTER,
              "artifact_sha256": sha256(directory / "parser.py") if source is not None else None,
              "provenance": "author-supplied; agent execution and costs not independently verified",
              "artifact_verdict": artifact_verdict,
              "evidence_sha256": {name: sha256(directory / name) for name in compare.EVIDENCE
                                  if (directory / name).is_file()}}
    record["evidence_sha256"]["author-execution.json"] = sha256(directory / "author-execution.json")
    write_json(directory / "result.json", record)
    write_json(directory / "receipt.json", {"result_sha256": sha256(directory / "result.json")})
    return compare.report(output)
