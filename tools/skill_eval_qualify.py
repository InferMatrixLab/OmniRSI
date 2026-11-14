"""Qualify the existing artifact verifier with trusted software controls only.

No model, external artifact, custom grader or author execution is accepted here.
This command deliberately fails rather than skipping unavailable isolation.
"""
import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
from pathlib import Path

from inferrsi.apps import skill_eval_engineering as artifact_check
from inferrsi.benchmark import skill_eval_engineering as engineering
from inferrsi.harness import artifact_sandbox

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "tests/fixtures/skill_eval_engineering/correct_parser.py"


def qualify(revision, image):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("An exact checked-out revision is required")
    result = {
        "schema": "engineering-verifier-qualification-v1",
        "revision": revision, "runner_image": image,
        "platform": platform.system(), "python": platform.python_version(),
        "verdict": "INCONCLUSIVE", "controls": {},
        "scope": "Trusted software controls only; no model trial or author isolation acceptance",
        "verifier_sha256": hashlib.sha256(Path(engineering.__file__).read_bytes()).hexdigest(),
        "correct_control_sha256": hashlib.sha256(CONTROL.read_bytes()).hexdigest(),
    }
    bwrap = shutil.which("bwrap")
    result["bubblewrap_version"] = None
    if bwrap:
        version = subprocess.run([bwrap, "--version"], capture_output=True, timeout=5,
                                 env={"PATH": "/usr/bin:/bin", "LANG": "C"}, check=False)
        result["bubblewrap_version"] = version.stdout[:1024].decode("utf-8", errors="replace")
    task = {"id": "qualification-control", "split": "development",
            "request": engineering.REQUEST, "provenance": "Reviewed software controls"}
    try:
        for name, source in (("correct", CONTROL.read_text(encoding="utf-8")),
                             ("bad_starter", engineering.STARTER)):
            result["controls"][name] = artifact_check.verify(task, {
                "artifact": "parser.py", "summary": "Software control", "artifact_source": source,
            })
    except artifact_sandbox.SandboxUnavailable as error:
        result["error"] = str(error)
        return result
    correct, bad = result["controls"]["correct"], result["controls"]["bad_starter"]
    expected_names = {vector[0] for vector in engineering.VECTORS}
    passed = (correct["passed"] is True and set(correct["assertions"]) == expected_names
              and all(value is True for value in correct["assertions"].values())
              and bad["passed"] is False and set(bad["assertions"]) == expected_names
              and bad["assertions"].get("stream_error") is False)
    result["verdict"] = "PASS" if passed else "FAIL"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--runner-image", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    result = qualify(args.revision, args.runner_image)
    (args.output / "qualification.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(result["verdict"])
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
