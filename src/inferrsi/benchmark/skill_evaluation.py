# Skill-evaluation trial criteria and condition statistics. Pure rules over
# retained facts: no execution, no provider event parsing, no file access.


def valid_checks(checks) -> bool:
    return (
        isinstance(checks, dict)
        and isinstance(checks.get("passed"), bool)
        and isinstance(checks.get("assertions"), dict)
        and bool(checks["assertions"])
        and all(isinstance(value, bool) for value in checks["assertions"].values())
        and checks["passed"] == all(checks["assertions"].values())
    )


def task_verdict(execution: dict, trace: dict, output, checks) -> str:
    if execution["status"] != "completed" or trace["completed_turns"] != 1 or trace["errors"]:
        return "INCONCLUSIVE"
    if not valid_checks(checks):
        return "INCONCLUSIVE"
    return "PASS" if isinstance(output, dict) and checks["passed"] else "FAIL"


def summarize(trials: list[dict]) -> dict:
    """Per-condition counts and N/E pairs; each trial carries already normalized usage."""
    groups = {}
    for condition in sorted({t["condition"] for t in trials}):
        selected = [t for t in trials if t["condition"] == condition]
        usage = [t["observations"]["usage"] for t in selected]
        groups[condition] = {
            "trials": len(selected),
            "verdicts": {
                v: sum(t["verdict"] == v for t in selected)
                for v in ("PASS", "FAIL", "INCONCLUSIVE")
            },
            "evaluation_errors": sum(t.get("evaluation_error") is not None for t in selected),
            "elapsed_seconds": sum(t["elapsed_seconds"] for t in selected)
            if all(t["elapsed_seconds"] is not None for t in selected)
            else None,
            "input_tokens": sum(u["input_tokens"] for u in usage)
            if all(u and u.get("input_tokens") is not None for u in usage) else None,
            "output_tokens": sum(u["output_tokens"] for u in usage)
            if all(u and u.get("output_tokens") is not None for u in usage) else None,
            "cost": None,
        }
    paired = []
    baseline = {(t["task_id"], t["repeat"]): t for t in trials if t["condition"] == "N"}
    for trial in trials:
        key = trial["task_id"], trial["repeat"]
        if trial["condition"].startswith("E") and key in baseline:
            before = baseline[key]["verdict"]
            paired.append(
                {
                    "task_id": key[0],
                    "repeat": key[1],
                    "condition": trial["condition"],
                    "N": before,
                    "E": trial["verdict"],
                    "regression": before == "PASS" and trial["verdict"] == "FAIL",
                    "recovery": before == "FAIL" and trial["verdict"] == "PASS",
                }
            )
    return {"conditions": groups, "pairs": paired, "promotion": "not_evaluated"}
