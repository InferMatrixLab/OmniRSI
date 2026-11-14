"""Strict two-parser data ingress and isolated replay; never imports submitted code."""
import argparse
import hashlib
import json
import re
import urllib.request
from pathlib import Path

from inferrsi.apps import skill_eval_engineering as artifact_check
from inferrsi.benchmark import skill_eval_engineering as engineering
from inferrsi.harness import artifact_sandbox
from tools import skill_eval_artifact_guidance as artifact_guidance
from tools.skill_eval_qualify import qualify

SCHEMA = "engineering-cloud-replay-v1"
BUNDLE_PATH = "artifacts/skill-eval-cloud/bundle.json"
MAX_BUNDLE_BYTES = 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require_hash(value, length=64):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{%d}" % length, value):
        raise ValueError("Invalid exact digest/revision")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def validate_bundle(raw, expected_digest):
    require_hash(expected_digest)
    if len(raw) > MAX_BUNDLE_BYTES or digest(raw) != expected_digest:
        raise ValueError("Bundle size or digest mismatch")
    try:
        bundle = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as error:
        raise ValueError("Invalid bounded UTF-8 JSON") from error
    keys = {"schema_version", "task_id", "request_sha256", "grader_sha256",
            "inputs_sha256", "protocol_sha256", "artifacts"}
    if type(bundle) is not dict or set(bundle) != keys or bundle["schema_version"] != SCHEMA:
        raise ValueError("Unsupported bundle schema")
    if (type(bundle["task_id"]) is not str
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", bundle["task_id"])):
        raise ValueError("Invalid task identifier")
    for key in ("request_sha256", "grader_sha256", "inputs_sha256", "protocol_sha256"):
        require_hash(bundle[key])
    if (bundle["request_sha256"] != digest(engineering.REQUEST.encode("utf-8"))
            or bundle["grader_sha256"] != digest(Path(engineering.__file__).read_bytes())):
        raise ValueError("Frozen request/grader differs from trusted controller")
    artifacts = bundle["artifacts"]
    if type(artifacts) is not list or len(artifacts) != 2:
        raise ValueError("Exactly two arms required")
    for artifact, arm in zip(artifacts, ("baseline", "candidate")):
        if (type(artifact) is not dict or set(artifact) != {"arm", "source", "sha256"}
                or artifact["arm"] != arm or type(artifact["source"]) is not str):
            raise ValueError("Invalid ordered artifact schema")
        require_hash(artifact["sha256"])
        try:
            source = artifact["source"].encode("utf-8")
        except UnicodeError as error:
            raise ValueError("Invalid source UTF-8") from error
        if (not 0 < len(source) <= engineering.MAX_SOURCE_BYTES
                or digest(source) != artifact["sha256"]):
            raise ValueError("Artifact size or digest mismatch")
    return bundle


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected ingress redirect")


def fetch_bundle(commit):
    require_hash(commit, 40)
    url = f"https://raw.githubusercontent.com/InferMatrixLab/InferRSI/{commit}/{BUNDLE_PATH}"
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(url, timeout=20) as response:
        return response.read(MAX_BUNDLE_BYTES + 1)


def replay(raw, expected_digest, revision, image):
    bundle = validate_bundle(raw, expected_digest)
    receipt = {
        "schema_version": SCHEMA, "bundle_sha256": expected_digest,
        "provenance": {key: bundle[key] for key in
                       ("task_id", "request_sha256", "grader_sha256",
                        "inputs_sha256", "protocol_sha256")},
        "qualification": qualify(revision, image), "verdict": "INCONCLUSIVE", "artifacts": [],
        "scope": "Public parser replay delivery only; captures and comparability unchanged",
    }
    if receipt["qualification"]["verdict"] != "PASS":
        return receipt
    task = {"id": bundle["task_id"], "split": "development", "request": engineering.REQUEST,
            "provenance": "Fixed public reconstructed parser task; retained cloud artifacts"}
    try:
        for artifact in bundle["artifacts"]:
            checked = artifact_check.verify(task, {
                "artifact": "parser.py", "summary": "Retained artifact",
                "artifact_source": artifact["source"],
            })
            receipt["artifacts"].append({"arm": artifact["arm"], "sha256": artifact["sha256"],
                                         "result": checked})
    except artifact_sandbox.SandboxUnavailable:
        receipt["error"] = "Artifact sandbox unavailable; no fallback"
        return receipt
    # PASS describes complete replay delivery, NOT that either parser passed or a skill improved.
    receipt["verdict"] = "PASS"
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-commit", required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--runner-image", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    require_hash(args.evidence_commit, 40)
    require_hash(args.bundle_sha256)
    require_hash(args.revision, 40)
    args.output.mkdir(parents=True, exist_ok=False)
    try:
        receipt = replay(fetch_bundle(args.evidence_commit), args.bundle_sha256,
                         args.revision, args.runner_image)
    except Exception as error:
        # Never log exception text that may include input/source; preserve a failure category only.
        receipt = {"schema_version": SCHEMA, "verdict": "INCONCLUSIVE",
                   "error_category": type(error).__name__}
    receipt["guidance"] = artifact_guidance.replay(receipt)
    receipt["evidence_commit"] = args.evidence_commit
    receipt["controller_revision"] = args.revision
    (args.output / "replay.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    (args.output / "replay.md").write_text(artifact_guidance.markdown(receipt["guidance"]),
                                         encoding="utf-8")
    print(receipt["verdict"])
    return 0 if receipt["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
