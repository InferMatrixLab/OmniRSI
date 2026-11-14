"""Export two retained generation artifacts as inert, bounded replay input."""
import argparse
import hashlib
import json
from pathlib import Path

from inferrsi.benchmark import skill_eval_engineering as engineering
from inferrsi.harness import artifact_sandbox
from inferrsi.infra.artifacts import sha256
from tools import skill_eval_compare as compare


def export(output: Path, protocol: Path) -> dict:
    plan = compare.load_plan(output)
    if (plan.get("execution_mode") != engineering.GENERATION_MODE
            or plan.get("adapter") != engineering.ADAPTER):
        raise ValueError("Expected frozen generation-only engineering plan")
    schedule = plan["schedule"]
    if (len(schedule) != 2 or [e["condition"] for e in schedule] != list(compare.ROLES)
            or len({(e["task_id"], e["repeat"], e["split"]) for e in schedule}) != 1):
        raise ValueError("Expected exactly one ordered baseline/candidate pair")
    tasks = compare.read_json(output / "plan/tasks.json")
    if len(tasks) != 1 or tasks[0]["request"] != engineering.REQUEST:
        raise ValueError("Replay supports the fixed public engineering task only")
    approval = json.loads(artifact_sandbox.read_regular(
        output / "historical-execution-approval.json", 65536))
    if (approval.get("protocol_sha256") != sha256(protocol)
            or approval.get("plan_sha256") != sha256(output / "plan/freeze.json")
            or any(approval.get(role) != plan["arms"][role]["git_revision"]
                   for role in compare.ROLES)):
        raise ValueError("Historical approval does not bind this protocol and frozen pair")
    artifacts = []
    for entry in schedule:
        trial = compare.retained_trial(output, entry, engineering.ADAPTER,
                                       engineering.GENERATION_MODE)
        if (trial.get("missing") or trial.get("execution_mode") != engineering.GENERATION_MODE
                or trial.get("evaluation_error") not in (None, "artifact_sandbox_unavailable",
                                                         "artifact_verification_deferred")
                or trial.get("observations", {}).get("usage_complete") is not True):
            raise ValueError("Incomplete or invalid retained generation evidence")
        data = artifact_sandbox.read_regular(
            compare.trial_directory(output, entry) / "parser.py", engineering.MAX_SOURCE_BYTES)
        if not data:
            raise ValueError("Empty artifact")
        artifacts.append({"arm": entry["condition"], "source": data.decode("utf-8"),
                          "sha256": hashlib.sha256(data).hexdigest()})
    # This digest binds the full frozen input inventory, not a claim that a remote
    # reviewer independently inspected skill inputs or provider execution.
    inputs = json.dumps(plan["files"], sort_keys=True, separators=(",", ":")).encode()
    return {"schema_version": "engineering-cloud-replay-v1", "task_id": schedule[0]["task_id"],
            "request_sha256": hashlib.sha256(engineering.REQUEST.encode()).hexdigest(),
            "grader_sha256": sha256(Path(engineering.__file__)),
            "inputs_sha256": hashlib.sha256(inputs).hexdigest(),
            "protocol_sha256": sha256(protocol), "artifacts": artifacts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bundle = export(args.run, args.protocol)
    payload = json.dumps(bundle, ensure_ascii=True, sort_keys=True, indent=2).encode() + b"\n"
    if len(payload) > 1024 * 1024:
        raise ValueError("Replay bundle exceeds byte bound")
    # No replacement of an existing evidence file or accidental publication.
    with args.output.open("xb") as stream:
        stream.write(payload)
    print(json.dumps({"bundle_sha256": hashlib.sha256(payload).hexdigest()}))


if __name__ == "__main__":
    main()
