# Acceptance rules for one bounded, verifier-guided improvement step. Pure
# judgments over retained reports and normalized usage totals; no execution.

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SelectionPolicy:
    min_success_rate: float
    max_regressions: int

    def __post_init__(self):
        if (type(self.min_success_rate) not in (int, float)
                or not math.isfinite(self.min_success_rate)
                or not 0 <= self.min_success_rate <= 1):
            raise ValueError("min_success_rate must be finite and within [0, 1]")
        if type(self.max_regressions) is not int or self.max_regressions < 0:
            raise ValueError("max_regressions must be a nonnegative integer")


def assessment(report: dict, policy: SelectionPolicy, usage: dict) -> dict:
    """Only complete, receipt-verified matched evidence can qualify a successor.

    ``usage`` holds the normalized totals of the baseline and the candidate trials.
    """
    pairs = report["pairs"]
    base = [p["baseline"] for p in pairs]
    candidate = [p["candidate"] for p in pairs]
    passed = sum(t["verdict"] == "PASS" for t in candidate)
    base_passed = sum(t["verdict"] == "PASS" for t in base)
    regressions = sum(p["outcome"] == "regression" for p in pairs)
    complete = (bool(pairs) and report["status"]["execution_state"] == "completed"
                and all(p["outcome"] not in ("missing_pair", "inconclusive") for p in pairs))
    known = all(usage[role]["total_tokens"] is not None for role in usage)
    reason = "eligible"
    if not complete:
        reason = "incomplete_evidence"
    elif not known:
        reason = "unknown_usage"
    elif passed < base_passed or passed / len(pairs) < policy.min_success_rate:
        reason = "quality_floor"
    elif regressions > policy.max_regressions:
        reason = "regression_limit"
    elif passed == base_passed and usage["candidate"]["total_tokens"] >= (
            usage["baseline"]["total_tokens"]):
        reason = "no_improvement"
    return {"eligible": reason == "eligible", "reason": reason, "complete": complete,
            "successes": passed, "baseline_successes": base_passed,
            "trials": len(pairs), "regressions": regressions, "usage": usage}


def search_block(scores: dict[str, dict], reports: dict[str, dict]) -> str | None:
    """Why these probes cannot drive a selection: uncertain evidence, then baseline drift."""
    if any(s["reason"] in ("incomplete_evidence", "unknown_usage") for s in scores.values()):
        return "uncertain_probe"
    signatures = {
        tuple((p["case_id"], p["repeat"], p["baseline"]["verdict"]) for p in r["pairs"])
        for r in reports.values()
    }
    if len(signatures) > 1:
        return "unstable_baseline"
    return None


def holdout_accepts(score: dict) -> bool:
    # Holdout checks acceptance, not a new search/ranking opportunity. Equal quality/cost is OK.
    return score["reason"] == "eligible" or (score["reason"] == "no_improvement" and
        score["usage"]["candidate"]["total_tokens"] == score["usage"]["baseline"]["total_tokens"])


def rerun_assessment(score: dict, report: dict, probes: dict[str, dict],
                     scores: dict[str, dict]) -> dict:
    """A rerun stays eligible only on the probes' baseline and above every eligible probe.

    ``score`` and ``report`` describe the rerun; ``scores`` assess ``probes`` by name.
    """
    if not score["eligible"]:
        return score
    if search_block({**scores, "rerun": score}, {**probes, "rerun": report}) == (
            "unstable_baseline"):
        return {**score, "eligible": False, "reason": "unstable_baseline"}
    rank = (-score["successes"], score["usage"]["candidate"]["total_tokens"])
    for other in scores.values():
        if other["eligible"] and (
                -other["successes"], other["usage"]["candidate"]["total_tokens"]) < rank:
            return {**score, "eligible": False, "reason": "worse_than_eligible_probe"}
    return score
