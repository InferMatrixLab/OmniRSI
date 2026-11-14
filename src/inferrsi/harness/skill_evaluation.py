import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import artifact_sandbox
from .atomic_json import atomic_write_json
from .input_binding import file_sha256
from .local_process import execute_command
from .usage import normalize_usage as normalize_usage


def global_skills() -> list[str]:
    # Disable global skills for this client only; do not modify user configuration.
    return sorted(
        str(path.resolve())
        for root in (Path.home() / ".codex/skills", Path.home() / ".agents/skills")
        if root.is_dir()
        for path in root.rglob("SKILL.md")
    )


def client_config() -> dict:
    return {
        "sandbox_mode": "danger-full-access",
        "approval_policy": "never",
        "project_doc_max_bytes": 0,
        "web_search": "disabled",
        "features.apps": False,
        "features.plugins": False,
        "features.memories": False,
        "features.browser_use": False,
        "features.computer_use": False,
        "features.multi_agent": False,
        "features.daemon_auto_start": False,
        "skills.bundled.enabled": False,
        "skills.config": [{"path": path, "enabled": False} for path in global_skills()],
    }


def toml_value(value) -> str:
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k) + "=" + toml_value(v) for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(toml_value(item) for item in value) + "]"
    return json.dumps(value)


@dataclass(frozen=True)
class CodexClient:
    model: str
    effort: str
    timeout_seconds: float
    executable: str = "codex"

    def command(self, workspace: Path, final: Path, schema: Path, prompt: str) -> tuple[str, ...]:
        values = {**client_config(), "model_reasoning_effort": self.effort}
        argv = [
            self.executable,
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--json",
            "--skip-git-repo-check",
            "--model",
            self.model,
            "--cd",
            str(workspace),
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(final),
        ]
        for key, value in values.items():
            argv.extend(("-c", key + "=" + toml_value(value)))
        return (*argv, prompt)


def trace_metrics(path: Path) -> dict:
    # Missing usage stays unknown; cached_input is a subset of input.
    usage, commands, errors, completed = None, [], [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["type"] == "turn.completed":
            completed += 1
            usage = normalize_usage(event.get("usage"))
        elif event["type"] in ("turn.failed", "error"):
            errors.append(event)
        elif event["type"] == "item.completed":
            item = event["item"]
            if item["type"] == "command_execution":
                commands.append(item)
    return {"usage": usage, "commands": commands, "errors": errors, "completed_turns": completed}


def write_files(destination: Path, content: dict[str, bytes]) -> None:
    """Retain named byte payloads under ``destination``; the caller resolved their source."""
    for name, data in content.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


@dataclass(frozen=True)
class ArtifactPort:
    """Task-owned artifact definition and judgment, supplied by the composing application."""

    kind: str
    generation_mode: str
    starter: bytes
    max_source_bytes: int
    verify: Callable[[dict, dict], dict]


def run_trial(
    client: CodexClient,
    task: dict,
    condition: str,
    repeat: int,
    workspace: Path,
    trial_dir: Path,
    schema: Path,
    verifier: Path,
    prompt: str,
    deadline: float,
    *,
    valid_checks: Callable[[object], bool],
    task_verdict: Callable[[dict, dict, object, object], str],
    artifact: ArtifactPort | None = None,
    timeout_seconds: float | None = None,
    generation_only: bool = False,
) -> dict:
    """Execute one trial and retain its facts.

    Check validity, the task verdict and the artifact judgment are injected ports;
    this module holds no scoring rule and no task definition.
    """
    if generation_only and artifact is None:
        raise ValueError("Generation-only requires the engineering artifact adapter")
    trial_dir.mkdir(parents=True)
    final = trial_dir / "answer.json"
    before = artifact_sandbox.input_hashes(workspace) if artifact is not None else {
        p.relative_to(workspace).as_posix(): file_sha256(p)[0]
        for p in workspace.rglob("*") if p.is_file()
    }
    command = client.command(workspace, final, schema, prompt)
    # An injected client may tighten the frozen limit, but cannot extend it.
    timeout = client.timeout_seconds if timeout_seconds is None else min(
        client.timeout_seconds, timeout_seconds)
    agent_environment = getattr(client, "environment", None)
    options = {"environment": agent_environment()} if agent_environment else {}
    execution = execute_command(
        command,
        workspace,
        trial_dir / "agent",
        min(deadline, time.monotonic() + timeout),
        **options,
    )
    atomic_write_json(trial_dir / "execution.json", execution)
    try:
        parser = getattr(client, "trace_metrics", trace_metrics)
        trace = parser(trial_dir / "agent/stdout.log")
    except (ValueError, KeyError, TypeError, FileNotFoundError) as error:
        trace = {
            "usage": None,
            "commands": [],
            "errors": [type(error).__name__],
            "completed_turns": 0,
        }
    evaluation_error = None
    if execution["status"] != "completed":
        evaluation_error = "agent_" + execution["status"]
    elif trace["completed_turns"] != 1 or trace["errors"]:
        evaluation_error = "agent_trace_error"
    output, checks, verification = None, None, None
    if evaluation_error is None:
        if final.is_symlink():
            evaluation_error = "invalid_output_symlink"
        elif not final.is_file():
            evaluation_error = "missing_output"
        else:
            try:
                answer_limit = 1048576 if generation_only else 65536
                answer_bytes = (artifact_sandbox.read_regular(final, answer_limit)
                                if artifact is not None
                                else final.read_bytes())
                output = json.loads(answer_bytes.decode("utf-8"))
            except (ValueError, UnicodeError):
                evaluation_error = "invalid_output_json"
    if evaluation_error is None and generation_only:
        try:
            output = artifact_sandbox.materialize_generated_artifact(
                workspace, output, artifact.starter, artifact.max_source_bytes)
        except (ValueError, OSError):
            evaluation_error = "invalid_generated_artifact"
    if evaluation_error is None and artifact is not None:
        submitted = workspace / "parser.py"
        if not isinstance(output, dict) or set(output) != {"artifact", "summary"}:
            evaluation_error = "invalid_artifact_answer"
        else:
            try:
                artifact_bytes = artifact_sandbox.read_regular(
                    submitted, artifact.max_source_bytes)
                source = artifact_bytes.decode("utf-8")
                if any(path.is_symlink() for path in (trial_dir, *trial_dir.absolute().parents)):
                    raise ValueError("Retained directory contains a symlink")
                if any((trial_dir / name).exists() or (trial_dir / name).is_symlink()
                       for name in ("agent-answer.json", "parser.py", "verification")):
                    raise ValueError("Agent created reserved evidence paths")
            except (ValueError, UnicodeError):
                evaluation_error = "missing_or_invalid_artifact"
            else:
                # A single bounded snapshot supplies both retained bytes and grading.
                (trial_dir / "agent-answer.json").write_bytes(answer_bytes)
                (trial_dir / "parser.py").write_bytes(artifact_bytes)
                output = {**output, "artifact_source": source}
                atomic_write_json(final, output)
                verification_dir = trial_dir / "verification"
                verification_dir.mkdir()
                (verification_dir / "stdout.log").write_text("", encoding="utf-8")
                (verification_dir / "stderr.log").write_text("", encoding="utf-8")
                if generation_only:
                    # The generation process may carry provider credentials. Even
                    # a capable host must not execute generated source in this phase.
                    evaluation_error = "artifact_verification_deferred"
                else:
                    try:
                        checks = artifact.verify(task, output)
                    except artifact_sandbox.SandboxUnavailable:
                        evaluation_error = "artifact_sandbox_unavailable"
                    else:
                        atomic_write_json(verification_dir / "result.json", checks)
                        verification = {"status": "completed" if checks["passed"] else "failed",
                                        "returncode": 0 if checks["passed"] else 1}
    if evaluation_error is None and artifact is None:
        verifier_environment = getattr(client, "verifier_environment", None)
        options = {"environment": verifier_environment()} if verifier_environment else {}
        verification = execute_command(
            (sys.executable, str(verifier), str(workspace / "task.json"), str(final)),
            workspace,
            trial_dir / "verification",
            min(deadline, time.monotonic() + 30),
            **options,
        )
        result_path = trial_dir / "verification/result.json"
        if verification["status"] not in ("completed", "failed"):
            evaluation_error = "verifier_" + verification["status"]
        elif not result_path.is_file():
            evaluation_error = "missing_verifier_result"
        else:
            try:
                raw_checks = json.loads(result_path.read_text(encoding="utf-8"))
            except ValueError:
                evaluation_error = "invalid_verifier_result"
            else:
                if not valid_checks(raw_checks):
                    evaluation_error = "invalid_verifier_result"
                else:
                    # Keep raw results in verification/result.json; accept validated fields only.
                    checks = {key: raw_checks[key] for key in ("passed", "assertions")}
                    if verification["returncode"] != (0 if checks["passed"] else 1):
                        evaluation_error = "verifier_exit"
    try:
        after = artifact_sandbox.input_hashes(workspace) if artifact is not None else {
            p.relative_to(workspace).as_posix(): file_sha256(p)[0]
            for p in workspace.rglob("*") if p.is_file()
        }
    except (ValueError, OSError):
        after = None
        evaluation_error = evaluation_error or "invalid_workspace_node"
    verdict = task_verdict(execution, trace, output, checks)
    if before != after:
        evaluation_error = evaluation_error or "inputs_changed"
    if evaluation_error is not None:
        verdict = "INCONCLUSIVE"
    record = {
        "task_id": task["id"],
        "condition": condition,
        "repeat": repeat,
        "split": task["split"],
        "execution_state": execution["status"],
        "verdict": verdict,
        "evaluation_error": evaluation_error,
        "checks": checks,
        "verification": verification,
        "elapsed_seconds": execution["elapsed_seconds"],
        "observations": trace,
        "inputs_unchanged": before == after,
        "input_hashes": before,
        "output_sha256": (file_sha256(final)[0]
                          if final.is_file() and not final.is_symlink() else None),
    }
    if artifact is not None:
        retained = trial_dir / "parser.py"
        record["artifact_sha256"] = (file_sha256(retained)[0] if retained.is_file()
                                     and not retained.is_symlink() else None)
        record["artifact_kind"] = artifact.kind
        if generation_only:
            record["execution_mode"] = artifact.generation_mode
    atomic_write_json(trial_dir / "result.json", record)
    return record
