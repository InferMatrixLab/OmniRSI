"""Offline single-step harness policy above skill_eval_compare.

The Python adapter accepts trusted fixed-output clients only. There is deliberately
no real-client execution CLI: external process isolation is not implemented here.
"""

import copy
import io
import os
import subprocess
import tempfile
import time
from pathlib import Path

from inferrsi.apps.improvement import assessment, choose, holdout_accepts, rerun_assessment
from inferrsi.benchmark.improvement import SelectionPolicy
from inferrsi.harness.skill_evaluation import CodexClient
from inferrsi.harness.usage import usage_totals
from inferrsi.infra.artifacts import sha256, write_json
from inferrsi.knowledge_operations import KnowledgeReader, safe_path
from tools import skill_eval_compare as compare

CONTROLLERS = (*compare.CONTROLLERS, "tools/skill_eval_improve.py",
               "src/inferrsi/apps/improvement.py", "src/inferrsi/benchmark/improvement.py",
               "src/inferrsi/harness/improvement.py")


def frontmatter_prefix(data: bytes) -> bytes:
    # Match the catalog's delimiter-line rule, including CRLF and surrounding whitespace.
    stream = io.BytesIO(data)
    prefix = stream.readline()
    if prefix.strip() != b"---":
        raise ValueError("Skill must start with front matter")
    for line in stream:
        prefix += line
        if line.strip() == b"---":
            return prefix
    raise ValueError("Unclosed skill front matter")


def materialize(repo: Path, baseline: str, skill_root: str, skill: str,
                proposals: dict, allowed_paths: list[str]) -> tuple[dict, list[str]]:
    """Write immutable Git objects using a private index; never change a checkout/ref."""
    if set(proposals) - {"inv", "dom"}:
        raise ValueError("At most one inv and one dom proposal are supported")
    if len(set(allowed_paths)) != len(allowed_paths):
        raise ValueError("Repeated allowlist path")
    with tempfile.TemporaryDirectory(prefix="improve-snapshot-") as temporary:
        root = Path(temporary)
        library = compare.snapshot(repo, baseline, skill_root, root / "source")
        bundle = KnowledgeReader(skill_root=library).read("checkout:" + skill)
        for name in allowed_paths:
            safe_path(name)
            if name not in bundle.content or Path(name).suffix not in (".md", ".txt"):
                raise ValueError("Only existing bundled Markdown/text resources may be edited")
            bundle.content[name].decode("utf-8")
        for edits in proposals.values():
            if not isinstance(edits, dict) or set(edits) - set(allowed_paths):
                raise ValueError("Candidate edit outside frozen allowlist")
            for name, text in edits.items():
                if not isinstance(text, str) or "\0" in text:
                    raise ValueError("Candidate resources must be UTF-8 text")
                if Path(name).name == "SKILL.md" and frontmatter_prefix(
                        text.encode("utf-8")) != frontmatter_prefix(bundle.content[name]):
                    raise ValueError("Skill metadata must remain unchanged")
                if text.encode("utf-8") == bundle.content[name]:
                    raise ValueError("Proposal must change resource bytes")
        inv, dom = proposals.get("inv", {}), proposals.get("dom", {})
        conflicts = sorted(name for name in inv.keys() & dom.keys() if inv[name] != dom[name])
        candidates = {name: edits for name, edits in proposals.items() if edits}
        if inv and dom and not conflicts:
            candidates["composed"] = {**inv, **dom}
        revisions = {"h0": baseline}
        # Object plumbing avoids hooks, checkout filters and branch/index mutations.
        env = {**os.environ, "GIT_INDEX_FILE": str(root / "index"),
               "GIT_AUTHOR_NAME": "InferRSI Snapshot", "GIT_COMMITTER_NAME": "InferRSI Snapshot",
               "GIT_AUTHOR_EMAIL": "snapshot@example.invalid",
               "GIT_COMMITTER_EMAIL": "snapshot@example.invalid",
               "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
               "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z"}

        def git(*args, data=None):
            return subprocess.run([*compare.GIT_READ, *args], cwd=repo, env=env,
                                  input=data, capture_output=True, check=True).stdout.strip()

        for name, edits in candidates.items():
            git("read-tree", baseline)
            for path, text in sorted(edits.items()):
                blob = git("hash-object", "-w", "--stdin", data=text.encode("utf-8"))
                entry = b"100644 " + blob + b"\t" + (skill_root + "/" + path).encode() + b"\0"
                git("update-index", "-z", "--index-info", data=entry)
            tree = git("write-tree").decode("ascii")
            revisions[name] = git("-c", "commit.gpgsign=false", "commit-tree", tree,
                                  "-p", baseline, data=b"Declarative harness snapshot\n").decode()
        return revisions, conflicts


def prepare(config: dict, repo: Path, output: Path) -> str:
    """Freeze tasks, supplied proposals and selection/budget policy without model calls."""
    config = copy.deepcopy(config)
    if set(config) != {"skill", "skill_root", "baseline", "proposals", "allowed_paths",
                       "development", "holdout", "model", "limits", "selection"}:
        raise ValueError("Unknown or missing improvement configuration field")
    SelectionPolicy(**config["selection"])
    compare.require_git_version(subprocess.check_output(("git", "--version"), text=True))
    cases = config["development"] + config["holdout"]
    for split in ("development", "holdout"):
        if not config[split] or any(t.get("split") != split for t in config[split]):
            raise ValueError("Nonempty, disjoint development and holdout splits are required")
    comparison = {key: config[key] for key in ("skill", "skill_root", "model", "limits")}
    comparison.update(mode="update", cases="tasks.json", snapshots={
        role: {"git_revision": config["baseline"]} for role in compare.ROLES})
    compare.validate(comparison, cases)
    proposals = sum(bool(edits) for edits in config["proposals"].values())
    required_calls = 2 * config["limits"]["repeats"] * (
        (proposals + 1) * len(config["development"]) + len(config["holdout"]))
    if required_calls > config["limits"]["max_task_calls"]:
        raise ValueError("Whole loop exceeds max_task_calls (including rerun and holdout)")
    revisions, conflicts = materialize(
        repo, config["baseline"], config["skill_root"], config["skill"],
        config["proposals"], config["allowed_paths"])
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "freeze.json", {
        "format_version": 1, "config": config, "revisions": revisions,
        "conflicts": conflicts, "max_scheduled_calls": required_calls,
        "controllers": {name: sha256(compare.ROOT / name) for name in CONTROLLERS},
        "grader_sha256": sha256(compare.ROOT / "tools/skill_eval_verifier.py"),
        "execution": "trusted_offline_clients_only", "promotion": "none",
    })
    return sha256(output / "freeze.json")


def load_freeze(output: Path, digest: str) -> dict:
    if sha256(output / "freeze.json") != digest:
        raise ValueError("Improvement plan changed after freeze")
    plan = compare.read_json(output / "freeze.json")
    for name, expected in plan["controllers"].items():
        if sha256(compare.ROOT / name) != expected:
            raise ValueError("Improvement controller changed after freeze")
    if sha256(compare.ROOT / "tools/skill_eval_verifier.py") != plan["grader_sha256"]:
        raise ValueError("Improvement grader changed after freeze")
    return plan


def run_offline(repo: Path, output: Path, digest: str, *, client_factory) -> dict:
    """One exclusive attempt. The factory is trusted test code, never a candidate hook.

    No implicit client, retries, resume, proposal agent or holdout feedback loop.
    Hashes detect local mutation; they are not an OS sandbox or signatures.
    """
    plan = load_freeze(output, digest)
    config = plan["config"]
    policy = SelectionPolicy(**config["selection"])
    results = output / "results"
    results.mkdir()  # Exclusive claim also consumes interrupted attempts.
    start = time.monotonic()
    deadline = start + config["limits"]["campaign_seconds"]
    phases, reports, trials = {}, {}, []
    stopped = None

    def read_phase(phase):
        directory = results / phase
        if sha256(directory / "plan/freeze.json") != phases[phase]["plan_sha256"]:
            raise ValueError("Comparison plan changed after authorization")
        return compare.read_report(directory)

    def evaluate(phase, candidate, split):
        nonlocal stopped
        load_freeze(output, digest)
        if stopped:
            return None
        usage = usage_totals(trials)
        remaining_time = int(deadline - time.monotonic())
        remaining_calls = config["limits"]["max_task_calls"] - len(trials)
        remaining_tokens = config["limits"]["token_stop_new_trials"] - (
            usage["total_tokens"] or 0)
        required = 2 * len(config[split]) * config["limits"]["repeats"]
        if trials and usage["total_tokens"] is None:
            stopped = "unknown_usage"
        elif remaining_time <= 0 or remaining_calls < required or remaining_tokens <= 0:
            stopped = "total_budget"
        if stopped:
            return None
        phase_config = {key: config[key] for key in ("skill", "skill_root", "model")}
        phase_config.update(mode="update", cases=f"{phase}-tasks.json", snapshots={
            "baseline": {"git_revision": plan["revisions"]["h0"]},
            "candidate": {"git_revision": plan["revisions"][candidate]}}, limits={
                **config["limits"], "max_task_calls": remaining_calls,
                "campaign_seconds": remaining_time, "token_stop_new_trials": remaining_tokens})
        write_json(results / f"{phase}-tasks.json", config[split])
        write_json(results / f"{phase}-config.json", phase_config)
        directory = results / phase
        authorization = compare.prepare(results / f"{phase}-config.json", repo, directory)
        # Factory receives only phase and candidate names, never tasks, answers or gate results.
        client = client_factory(phase, candidate)
        if client is None or isinstance(client, CodexClient):
            raise ValueError("Only an explicitly injected trusted offline client is supported")
        compare.execute(directory, authorization, client=client, campaign_deadline=deadline,
                        stop_on_unknown_usage=True)
        phases[phase] = {"candidate": candidate, "plan_sha256": authorization,
                         "report": f"results/{phase}/report.json"}
        report = read_phase(phase)  # Revalidate retained bytes before selection.
        reports[phase] = report
        trials.extend(t for p in report["pairs"] for t in (p["baseline"], p["candidate"])
                      if not t["missing"])
        if report["status"].get("stop_reason") == "unknown_usage":
            stopped = "unknown_usage"
        elif report["status"]["execution_state"] != "completed":
            stopped = "incomplete_execution"
        elif time.monotonic() >= deadline:
            stopped = "total_budget"
        else:
            total_tokens = usage_totals(trials)["total_tokens"]
            if total_tokens is None:
                stopped = "unknown_usage"
            elif total_tokens > config["limits"]["token_stop_new_trials"]:
                stopped = "total_budget"
        return report

    for name in ("inv", "dom"):
        if name in plan["revisions"]:
            evaluate(name, name, "development")
    # Reload earlier probe evidence too: a later phase cannot invalidate it silently.
    probes = {name: read_phase(name) for name in ("inv", "dom")
              if name in reports}
    decision = choose(probes, policy, composable="composed" in plan["revisions"])
    successor = "h0" if stopped else decision["successor"]
    rerun = evaluate("rerun", successor, "development")
    frozen = successor if successor != "h0" and rerun and rerun_assessment(rerun, probes, policy)[
        "eligible"] and not stopped else "h0"
    # Improver-facing record is closed before holdout; contains no holdout inputs or outcomes.
    experience = {"parent": plan["revisions"]["h0"], "decision": decision,
                  "attempted_successor": successor, "frozen_candidate": frozen,
                  "rerun": rerun_assessment(rerun, probes, policy) if rerun else None,
                  "conflicts": plan["conflicts"], "applicability": "offline performance audit",
                  "promotion": "none", "transfer": "not_evaluated"}
    write_json(results / "experience.json", experience)
    write_json(results / "final.json", {"candidate": frozen,
                                        "git_revision": plan["revisions"][frozen]})
    final_digest = sha256(results / "final.json")
    gate = evaluate("holdout", frozen, "holdout")
    load_freeze(output, digest)
    if sha256(results / "final.json") != final_digest:
        raise ValueError("Final candidate changed during holdout")
    accepted = frozen != "h0" and gate is not None and not stopped and holdout_accepts(gate, policy)
    # Report refreshed evidence, including any late mutation, without another trial.
    for phase in reports:
        reports[phase] = read_phase(phase)
    if any(not assessment(r, policy)["complete"] for r in reports.values()):
        accepted = False
        stopped = stopped or "invalid_retained_evidence"
    all_trials = [t for r in reports.values() for p in r["pairs"]
                  for t in (p["baseline"], p["candidate"]) if not t["missing"]]
    search_trials = [t for phase, r in reports.items() if phase != "holdout"
                     for p in r["pairs"] for t in (p["baseline"], p["candidate"])
                     if not t["missing"]]
    selected_role = "candidate" if accepted else "baseline"
    selected_trials = [p[selected_role] for p in reports.get("holdout", {}).get("pairs", [])
                       if not p[selected_role]["missing"]]
    result = {"format_version": 1, "plan_sha256": digest, "final_sha256": final_digest,
              "decision": decision, "frozen_candidate": frozen,
              "selected": frozen if accepted else "h0", "accepted": accepted,
              "stop_reason": stopped, "phases": phases,
              "development": experience,
              "holdout": assessment(reports["holdout"], policy) if gate else None,
              "usage": {"search": usage_totals(search_trials), "total": usage_totals(all_trials),
                        "selected_holdout": usage_totals(selected_trials),
                        "proposal_model_calls": 0, "composition_model_calls": 0},
              "wall_seconds": time.monotonic() - start,
              "claim": "Fixed-output controller validation only; model efficacy and engine "
                       "performance are unmeasured. Monetary cost is unknown."}
    write_json(output / "report.json", result)
    return result
