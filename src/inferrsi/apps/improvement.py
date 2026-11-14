# Composed improvement-step entries: Harness usage totals and search progression
# joined with Benchmark acceptance rules. Signatures and results match the
# functions that harness.improvement held before the ownership split.

from inferrsi.benchmark import improvement as rules
from inferrsi.benchmark.improvement import SelectionPolicy
from inferrsi.harness import improvement as progression
from inferrsi.harness.usage import usage_totals


def assessment(report: dict, policy: SelectionPolicy) -> dict:
    pairs = report["pairs"]
    usage = {role: usage_totals([p[role] for p in pairs]) for role in ("baseline", "candidate")}
    return rules.assessment(report, policy, usage)


def choose(probes: dict[str, dict], policy: SelectionPolicy, *, composable: bool) -> dict:
    scores = {name: assessment(report, policy) for name, report in probes.items()}
    return progression.choose(
        scores, blocked=rules.search_block(scores, probes), composable=composable)


def holdout_accepts(report: dict, policy: SelectionPolicy) -> bool:
    return rules.holdout_accepts(assessment(report, policy))


def rerun_assessment(report: dict, probes: dict[str, dict], policy: SelectionPolicy) -> dict:
    scores = {name: assessment(probe, policy) for name, probe in probes.items()}
    return rules.rerun_assessment(assessment(report, policy), report, probes, scores)
