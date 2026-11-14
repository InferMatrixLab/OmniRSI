"""Offline, synthetic KV admission diagnosis contract; not an engine simulator.

Only observations cross the task boundary. The oracle stays in the validator,
not in the task or selected knowledge bundle. No provider or execution hooks.
"""
import json
import os
import re
import sys
from pathlib import Path

ADAPTER = "kv-admission-diagnostic-v1"
PROMPT = (
    "Read task.json and diagnose why the synthetic request cannot run. Knowledge is fallible "
    "guidance; observations are authoritative. Cite only decisive event IDs. For insufficient "
    "evidence cite all observed receive, identity and allocator events, or the lookup event "
    "only if none of those exists. Choose one next read-only "
    "check, and decide whether these observations authorize reusing KV now. Return only the "
    "required JSON. Inspect only this workspace; do not modify inputs, use network, execute "
    "repository code, run another agent, commit or publish. No service-performance claim is asked."
)
DIAGNOSES = ("readiness", "capacity", "invalid_reuse", "insufficient_evidence")
CHECKS = ("check_receive_completion", "inspect_allocator_accounting",
          "check_transfer_integrity", "collect_missing_events")
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["diagnosis", "evidence", "next_check", "reuse_authorized"],
    "properties": {
        "diagnosis": {"type": "string", "enum": list(DIAGNOSES)},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "next_check": {"type": "string", "enum": list(CHECKS)},
        "reuse_authorized": {"type": "boolean"},
    },
}
VALUES = {
    "cache_lookup": {"hit", "miss"},
    "receive": {"pending", "complete", "failed"},
    "identity": {"match", "mismatch"},
    "allocator": {"denied", "granted", "not_attempted"},
}


def validate_task(task):
    if not isinstance(task, dict) or set(task) != {
        "id", "split", "request", "provenance", "events"
    }:
        raise ValueError("Diagnostic task requires only id/split/request/provenance/events")
    if any(type(task[k]) is not str or not task[k].strip()
           for k in ("id", "split", "request", "provenance")):
        raise ValueError("Diagnostic task metadata must be nonempty strings")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*", task["id"]):
        raise ValueError("Diagnostic task ID must be a safe path component")
    if task["split"] not in ("development", "holdout", "discovery"):
        raise ValueError("Unknown case split")
    if not isinstance(task["events"], list) or not task["events"]:
        raise ValueError("Diagnostic observations must be a nonempty list")
    ids, kinds = set(), set()
    for event in task["events"]:
        if not isinstance(event, dict) or set(event) != {"id", "kind", "value"}:
            raise ValueError("Diagnostic event requires only id/kind/value")
        if any(type(value) is not str for value in event.values()):
            raise ValueError("Diagnostic event fields must be strings")
        if not re.fullmatch(r"E[0-9]+", event["id"]) or event["id"] in ids:
            raise ValueError("Diagnostic event IDs must be unique E-number identifiers")
        if event["kind"] not in VALUES or event["value"] not in VALUES[event["kind"]]:
            raise ValueError("Unknown diagnostic observation")
        if event["kind"] in kinds:
            raise ValueError("Use a single observation per kind at the decision boundary")
        ids.add(event["id"])
        kinds.add(event["kind"])


def expected(task):
    """Finite-state decision table with conservative unknown-state handling.

    An explicitly failed transfer or identity mismatch forbids reuse regardless
    of allocation. A pending receive is a readiness barrier, not capacity proof.
    Capacity attribution needs completed, matching KV and explicit denial.
    Anything else is insufficient to identify why admission is blocked.
    """
    validate_task(task)
    states = {e["kind"]: e["value"] for e in task["events"]}
    ids = {e["kind"]: e["id"] for e in task["events"]}
    invalid = {k for k, v in (("receive", "failed"), ("identity", "mismatch"))
               if states.get(k) == v}
    if invalid:
        return "invalid_reuse", {ids[k] for k in invalid}, "check_transfer_integrity"
    if states.get("receive") == "pending":
        return "readiness", {ids["receive"]}, "check_receive_completion"
    if (states.get("receive"), states.get("identity"), states.get("allocator")) == (
        "complete", "match", "denied"
    ):
        return "capacity", {ids[k] for k in ("receive", "identity", "allocator")}, (
            "inspect_allocator_accounting")
    relevant = {event_id for kind, event_id in ids.items() if kind != "cache_lookup"}
    return "insufficient_evidence", relevant or set(ids.values()), "collect_missing_events"


def verify(task, answer):
    diagnosis, required, next_check = expected(task)
    valid = (isinstance(answer, dict) and set(answer) == set(SCHEMA["required"])
             and type(answer["diagnosis"]) is str and answer["diagnosis"] in DIAGNOSES
             and type(answer["next_check"]) is str and answer["next_check"] in CHECKS
             and type(answer["reuse_authorized"]) is bool
             and isinstance(answer["evidence"], list)
             and all(type(v) is str for v in answer["evidence"]))
    if not valid:
        return {"passed": False, "assertions": {"schema": False}}
    cited = set(answer["evidence"])
    checks = {
        "schema": True,
        "diagnosis": answer["diagnosis"] == diagnosis,
        "citation": cited == required
        and len(cited) == len(answer["evidence"]),
        "safe_next_check": answer["next_check"] == next_check,
        # These blocked-request observations never authorize executing reuse.
        "no_invariant_violation": answer["reuse_authorized"] is False,
    }
    return {"passed": all(checks.values()), "assertions": checks}


def main():
    task = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    answer = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    result = verify(task, answer)
    Path(os.environ["INFERRSI_RESULT_PATH"]).write_text(json.dumps(result), encoding="utf-8")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
