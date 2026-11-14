# Composed skill-evaluation entries: Knowledge bundle export, and Harness trial
# execution joined with Benchmark verdict rules. Signatures and retained records
# match the functions that harness.skill_evaluation held before the ownership split.

from pathlib import Path

from inferrsi.benchmark import skill_eval_engineering as engineering_task
from inferrsi.benchmark import skill_evaluation as rules
from inferrsi.harness import skill_evaluation as host
from inferrsi.harness.usage import normalize_usage
from inferrsi.knowledge.knowledge_operations import KnowledgeReader

from . import skill_eval_engineering


def export_bundle(reader: KnowledgeReader, reference: str, destination: Path) -> dict:
    # Knowledge resolves the bundle; Harness receives and retains bytes only.
    bundle = reader.read(reference)
    host.write_files(destination, bundle.content)
    return bundle.view()


def summarize(trials: list[dict]) -> dict:
    normalized = [
        {**trial, "observations": {**trial["observations"],
                                   "usage": normalize_usage(trial["observations"]["usage"])}}
        for trial in trials
    ]
    return rules.summarize(normalized)


def run_trial(
    client,
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
    timeout_seconds: float | None = None,
    engineering_artifact: bool = False,
    generation_only: bool = False,
) -> dict:
    artifact = None
    if engineering_artifact:
        artifact = host.ArtifactPort(
            kind=engineering_task.ADAPTER,
            generation_mode=engineering_task.GENERATION_MODE,
            starter=engineering_task.STARTER.encode("utf-8"),
            max_source_bytes=engineering_task.MAX_SOURCE_BYTES,
            verify=skill_eval_engineering.verify,
        )
    return host.run_trial(
        client, task, condition, repeat, workspace, trial_dir, schema, verifier, prompt,
        deadline, valid_checks=rules.valid_checks, task_verdict=rules.task_verdict,
        artifact=artifact, timeout_seconds=timeout_seconds, generation_only=generation_only,
    )
