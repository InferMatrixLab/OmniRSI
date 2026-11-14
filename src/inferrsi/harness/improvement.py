"""Bounded search progression over injected probe assessments; no execution or model access."""


def choose(scores: dict[str, dict], *, blocked: str | None, composable: bool) -> dict:
    """Quality first, then tokens; compose only two individually eligible edits.

    This lexicographic rule selects a Pareto member without arbitrary weights.
    Composition is only a proposal: its mandatory rerun may reject it.
    ``scores`` and ``blocked`` are injected assessments; no threshold is applied here.
    """
    if blocked is not None:
        return {"successor": "h0", "reason": blocked, "probes": scores}
    eligible = [name for name, score in scores.items() if score["eligible"]]
    if len(eligible) == 2 and composable:
        return {"successor": "composed", "reason": "compose_eligible", "probes": scores}
    winner = min(eligible, key=lambda name: (
        -scores[name]["successes"], scores[name]["usage"]["candidate"]["total_tokens"], name
    )) if eligible else "h0"
    return {"successor": winner, "reason": "quality_then_tokens" if eligible else "retain_h0",
            "probes": scores}
