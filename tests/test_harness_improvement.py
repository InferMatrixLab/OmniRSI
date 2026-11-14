import copy
import subprocess
import sys
from unittest.mock import patch

import pytest

from inferrsi.apps.improvement import assessment, choose, holdout_accepts
from inferrsi.benchmark.improvement import SelectionPolicy
from inferrsi.harness.skill_evaluation import CodexClient
from inferrsi.harness.usage import usage_totals
from tests.fixtures.harness_improvement_demo import ScriptedClient, configuration
from tests.fixtures.skill_eval_compare_support import git
from tools import skill_eval_compare as compare
from tools import skill_eval_improve as improve


def fixture(tmp_path):
    try:
        compare.require_git_version(subprocess.check_output(("git", "--version"), text=True))
    except ValueError as error:
        pytest.skip(str(error))
    return configuration(tmp_path)


def run(tmp_path, fault=None, change=None):
    config, repo = fixture(tmp_path)
    if change:
        change(config)
    output = tmp_path / "run"
    digest = improve.prepare(config, repo, output)
    clients = []

    def factory(phase, candidate):
        client = ScriptedClient(tmp_path, phase, fault)
        clients.append(client)
        return client

    result = improve.run_offline(repo, output, digest, client_factory=factory)
    return result, clients, output, repo, digest


def test_successful_loop_materializes_edits_and_accounts_for_all_stages(tmp_path):
    result, clients, output, repo, digest = run(tmp_path)
    assert result["selected"] == "composed" and result["accepted"]
    assert list(result["phases"]) == ["inv", "dom", "rerun", "holdout"]
    assert result["holdout"]["successes"] == 3
    assert result["holdout"]["baseline_successes"] == 1
    assert result["usage"]["total"]["total_tokens"] == 2010
    assert result["usage"]["total"]["calls"] == 24
    assert result["usage"]["search"]["total_tokens"] == 1530
    assert result["usage"]["selected_holdout"]["total_tokens"] == 180
    assert result["usage"]["total"]["cached_input_tokens"] == 240
    assert result["usage"]["total"]["tool_calls"] == 48
    assert result["usage"]["total"]["cost"] is None
    assert sum(len(client.calls) for client in clients) == 24
    assert not git(repo, "status", "--porcelain")
    plan = improve.load_freeze(output, digest)
    changed = git(repo, "diff", "--name-only", plan["revisions"]["h0"],
                  plan["revisions"]["composed"]).splitlines()
    assert changed == ["library/selected/SKILL.md", "library/selected/resource.txt"]
    # Independently checked answers, not the fixture's proposed correctness flag, drive verdicts.
    report = compare.read_report(output / "results/holdout")
    assert all(p["candidate"]["checks"]["passed"] for p in report["pairs"])
    assert "holdout" not in (output / "results/experience.json").read_text()
    assert all(not any("answers" in name or "verify.py" in name for name in call["files"])
               for client in clients for call in client.calls)
    assert all(call["task_id"].startswith("development-") for client in clients[:3]
               for call in client.calls)
    assert all(call["task_id"].startswith("holdout-") for call in clients[-1].calls)
    with pytest.raises(FileExistsError):
        improve.run_offline(repo, output, digest,
                            client_factory=lambda *args: pytest.fail("must not repeat holdout"))


@pytest.mark.parametrize("fault,successor,frozen", [
    ("worse", "h0", "h0"),
    ("compose_regression", "composed", "h0"),
    ("dominated", "composed", "h0"),
    ("holdout_regression", "composed", "composed"),
])
def test_degraded_candidates_rerun_and_holdout_retain_baseline(tmp_path, fault, successor, frozen):
    result, clients, output, _, _ = run(tmp_path, fault)
    assert result["selected"] == "h0" and not result["accepted"]
    assert result["development"]["attempted_successor"] == successor
    assert result["frozen_candidate"] == frozen
    assert [client.phase for client in clients] == ["inv", "dom", "rerun", "holdout"]
    assert result["usage"]["selected_holdout"]["total_tokens"] == 300
    if fault == "holdout_regression":
        assert result["holdout"]["successes"] == 0
        # No holdout result contaminates improvement experience or causes another search.
        experience = compare.read_json(output / "results/experience.json")
        assert experience["frozen_candidate"] == "composed"
        assert experience["rerun"]["successes"] == 3
    if fault == "dominated":
        assert result["development"]["rerun"]["reason"] == "worse_than_eligible_probe"


@pytest.mark.parametrize("fault", ["unknown_usage", "runner_error", "hidden_write"])
def test_unknown_usage_runner_failures_and_input_tampering_cannot_win(tmp_path, fault):
    result, _, _, _, _ = run(tmp_path, fault)
    assert not result["accepted"] and result["selected"] == "h0"
    assert result["decision"]["successor"] == "h0"
    if fault == "unknown_usage":
        assert result["usage"]["total"]["total_tokens"] is None
        assert result["stop_reason"] == "unknown_usage"
    if fault == "runner_error":
        assert result["stop_reason"] == "incomplete_execution"
        assert "rerun" not in result["phases"]  # Cannot invent an execution after stopping.


def test_conflicting_edits_select_one_and_still_rerun(tmp_path):
    def conflict(config):
        config["proposals"]["dom"] = {"selected/SKILL.md":
            config["proposals"]["inv"]["selected/SKILL.md"] + "A different INV policy\n"}

    result, _, _, _, _ = run(tmp_path, change=conflict)
    assert result["development"]["conflicts"] == ["selected/SKILL.md"]
    assert result["decision"]["successor"] in ("inv", "dom")
    assert result["phases"]["rerun"]["candidate"] == result["decision"]["successor"]


def test_no_proposals_still_reruns_h0(tmp_path):
    result, clients, _, _, _ = run(tmp_path, change=lambda c: c.update(proposals={}))
    assert result["selected"] == "h0"
    assert [client.phase for client in clients] == ["rerun", "holdout"]


def test_total_token_stop_applies_across_comparisons(tmp_path):
    result, _, _, _, _ = run(
        tmp_path, change=lambda c: c["limits"].update(token_stop_new_trials=600))
    assert result["selected"] == "h0" and result["stop_reason"] == "incomplete_execution"
    # First probe costs 540, second may cross 600 once, then no new trials/stages.
    assert result["usage"]["total"]["calls"] == 7
    assert result["usage"]["total"]["total_tokens"] == 640
    assert "rerun" not in result["phases"] and "holdout" not in result["phases"]


def test_expired_absolute_deadline_prevents_phase_calls(tmp_path):
    config, repo = fixture(tmp_path)
    output = tmp_path / "run"
    digest = improve.prepare(config, repo, output)

    def factory(phase, candidate):
        # Exercise shared executor's inherited absolute deadline, with no blocking wait.
        class LateClient(ScriptedClient):
            def command(self, *args):
                pytest.fail("expired executor must not call client")
        return LateClient(tmp_path, phase)

    original = compare.execute

    def expired(*args, **kwargs):
        kwargs["campaign_deadline"] = 0
        return original(*args, **kwargs)

    with patch.object(compare, "execute", expired):
        result = improve.run_offline(repo, output, digest, client_factory=factory)
    assert result["selected"] == "h0" and result["usage"]["total"]["calls"] == 0


@pytest.mark.parametrize("configured_timeout,client_timeout", [(1, 10), (10, 0.1)])
def test_frozen_and_shorter_client_timeouts_both_bound_offline_calls(
        tmp_path, configured_timeout, client_timeout):
    config, repo = fixture(tmp_path)
    config["limits"]["timeout_seconds"] = configured_timeout
    output = tmp_path / "run"
    digest = improve.prepare(config, repo, output)
    calls = []

    class DelayedClient:
        timeout_seconds = client_timeout

        def command(self, workspace, final, schema, prompt):
            calls.append(final)
            return (sys.executable, "-c", "import time; time.sleep(1.3)")

    result = improve.run_offline(repo, output, digest,
                                client_factory=lambda *args: DelayedClient())
    assert len(calls) == 1
    record = compare.read_json(calls[0].parent / "result.json")
    assert record["execution_state"] == "timeout"
    report = compare.read_report(output / "results/inv")
    assert report["status"]["stop_reason"] == "client_timeout"
    assert result["selected"] == "h0" and not result["accepted"]
    assert list(result["phases"]) == ["inv"]


@pytest.mark.parametrize("mutation", ["overlap", "mixed_split", "calls", "threshold", "extra"])
def test_reject_invalid_control_plan_before_execution(tmp_path, mutation):
    config, repo = fixture(tmp_path)
    if mutation == "overlap":
        config["holdout"][0]["id"] = config["development"][0]["id"]
    elif mutation == "mixed_split":
        config["development"][0]["split"] = "holdout"
    elif mutation == "calls":
        config["limits"]["max_task_calls"] = 12
    elif mutation == "threshold":
        config["selection"]["min_success_rate"] = float("nan")
    else:
        config["proposals"]["third"] = config["proposals"]["inv"]
    with pytest.raises(ValueError):
        improve.prepare(config, repo, tmp_path / "run")
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("path", ["../answers.json", "selected/metadata.json", "exam.txt",
                                  "selected/tool.py", "/task.json"])
def test_proposals_cannot_change_hidden_answers_metadata_or_executables(tmp_path, path):
    config, repo = fixture(tmp_path)
    config["allowed_paths"].append(path)
    config["proposals"]["inv"] = {path: "untrusted"}
    with pytest.raises(ValueError):
        improve.prepare(config, repo, tmp_path / "run")


@pytest.mark.parametrize("client", [None, CodexClient("unused", "test", 1)])
def test_plan_mutation_and_real_client_rejected(tmp_path, client):
    config, repo = fixture(tmp_path)
    output = tmp_path / "run"
    digest = improve.prepare(config, repo, output)
    original = (output / "freeze.json").read_bytes()
    (output / "freeze.json").write_bytes(original + b" ")
    with pytest.raises(ValueError, match="plan changed"):
        improve.run_offline(repo, output, digest, client_factory=None)
    (output / "freeze.json").write_bytes(original)
    with pytest.raises(ValueError, match="offline client"):
        improve.run_offline(repo, output, digest, client_factory=lambda *args: client)


@pytest.mark.parametrize("artifact", ["answer.json", "result.json", "agent/stdout.log"])
def test_late_probe_artifact_tampering_rolls_back(tmp_path, artifact):
    config, repo = fixture(tmp_path)
    output = tmp_path / "run"
    digest = improve.prepare(config, repo, output)

    def factory(phase, candidate):
        if phase == "dom":
            path = output / "results/inv/results/development-control-candidate-1" / artifact
            path.write_bytes(path.read_bytes() + b" ")
        return ScriptedClient(tmp_path, phase)

    result = improve.run_offline(repo, output, digest, client_factory=factory)
    assert result["selected"] == "h0" and result["decision"]["successor"] == "h0"
    assert result["stop_reason"] == "invalid_retained_evidence"


def test_phase_schedule_cannot_hide_failed_pair_after_authorization(tmp_path):
    config, repo = fixture(tmp_path)
    output = tmp_path / "run"
    digest = improve.prepare(config, repo, output)

    def factory(phase, candidate):
        if phase == "holdout":
            path = output / "results/inv/plan/freeze.json"
            plan = compare.read_json(path)
            plan["schedule"] = [e for e in plan["schedule"]
                                if e["task_id"] != "development-domain"]
            compare.write_json(path, plan)
        return ScriptedClient(tmp_path, phase)

    with pytest.raises(ValueError, match="plan changed after authorization"):
        improve.run_offline(repo, output, digest, client_factory=factory)
    assert not (output / "report.json").exists()


def test_rerun_with_unstable_baseline_is_rejected(tmp_path):
    from inferrsi.apps.improvement import rerun_assessment

    _, _, output, _, _ = run(tmp_path)
    probe = compare.read_report(output / "results/inv")
    rerun = compare.read_report(output / "results/rerun")
    rerun["pairs"][1]["baseline"]["verdict"] = "PASS"
    result = rerun_assessment(rerun, {"inv": probe}, SelectionPolicy(0.5, 0))
    assert not result["eligible"] and result["reason"] == "unstable_baseline"


@pytest.mark.parametrize("newline,description", [("\r\n", "fixture"),
                                                ("\n", "fixture --- embedded delimiter")])
def test_metadata_prefix_cannot_be_changed_via_crlf_or_embedded_delimiter(
        tmp_path, newline, description):
    config, repo = fixture(tmp_path)
    path = repo / "library/selected/SKILL.md"
    source = newline.join(("---", "name: selected", f"description: {description}",
                           "license: UNKNOWN", "---", "Old body", ""))
    path.write_bytes(source.encode())
    git(repo, "add", "library/selected/SKILL.md")
    git(repo, "commit", "--quiet", "-m", "Fixture metadata prefix")
    config["baseline"] = git(repo, "rev-parse", "HEAD")
    # Body-only changes remain valid with both supported front-matter encodings.
    config["proposals"]["inv"] = {"selected/SKILL.md": source.replace("Old body", "INV enabled")}
    improve.prepare(config, repo, tmp_path / "valid")
    config["proposals"]["inv"] = {"selected/SKILL.md": source.replace("UNKNOWN", "MIT")}
    with pytest.raises(ValueError, match="metadata must remain unchanged"):
        improve.prepare(config, repo, tmp_path / "invalid")


def test_policy_unknown_invalid_usage_and_quality_before_cost():
    trials = [{"observations": {"usage": {"input_tokens": 10, "output_tokens": 2,
                                          "cached_input_tokens": 4}, "tool_calls": 1},
               "elapsed_seconds": 0.1}]
    assert usage_totals(trials)["total_tokens"] == 12
    for value in (-1, True, float("nan"), None):
        bad = copy.deepcopy(trials)
        bad[0]["observations"]["usage"]["input_tokens"] = value
        assert usage_totals(bad)["total_tokens"] is None


def test_rules_and_progression_meet_only_through_injected_values():
    from inferrsi.benchmark import improvement as rules
    from inferrsi.harness import improvement as progression

    report = {"status": {"execution_state": "completed"}, "pairs": [{
        "case_id": "one", "repeat": 1, "outcome": "unchanged_correct",
        "baseline": {"verdict": "PASS"}, "candidate": {"verdict": "PASS"}}]}
    usage = {"baseline": {"total_tokens": 10}, "candidate": {"total_tokens": 8}}
    # Benchmark judges supplied totals; it reads no trial usage and imports no Harness.
    score = rules.assessment(report, SelectionPolicy(0.5, 0), usage)
    assert score["eligible"] and score["usage"] is usage
    assert rules.search_block({"inv": score}, {"inv": report}) is None
    # Harness advances on supplied assessments; it holds no threshold.
    assert progression.choose({"inv": score}, blocked=None, composable=False) == {
        "successor": "inv", "reason": "quality_then_tokens", "probes": {"inv": score}}
    assert progression.choose({"inv": score}, blocked="unstable_baseline", composable=True) == {
        "successor": "h0", "reason": "unstable_baseline", "probes": {"inv": score}}
    assert not hasattr(progression, "SelectionPolicy") and not hasattr(rules, "usage_totals")


def test_cost_selection_ignores_unknown_and_preserves_quality_floor(tmp_path):
    _, _, output, _, _ = run(tmp_path)
    report = compare.read_report(output / "results/inv")
    policy = SelectionPolicy(min_success_rate=0.5, max_regressions=0)
    assert assessment(report, policy)["eligible"]
    # More successes can justify greater cost; same quality needs lower known cost.
    assert choose({"inv": report}, policy, composable=False)["successor"] == "inv"
    report["pairs"][0]["candidate"]["observations"]["usage"] = None
    assert choose({"inv": report}, policy, composable=False)["successor"] == "h0"


@pytest.mark.parametrize("usage", ["malformed", [1]])
def test_malformed_usage_container_stays_unknown(usage):
    trial = {"observations": {"usage": usage}, "elapsed_seconds": 1}
    totals = usage_totals([trial])
    assert totals["total_tokens"] is None
    assert totals["input_tokens"] is None and totals["output_tokens"] is None


@pytest.mark.parametrize("fault", ["string_usage", "list_usage"])
def test_malformed_trace_usage_stops_calls_and_retains_h0(tmp_path, fault):
    result, clients, output, _, _ = run(tmp_path, fault)
    assert result["selected"] == "h0" and not result["accepted"]
    assert result["usage"]["total"]["total_tokens"] is None
    assert result["stop_reason"] == "unknown_usage"
    assert sum(len(client.calls) for client in clients) == 2
    assert list(result["phases"]) == ["inv"]
    # Reporting retained bytes must also work without another client invocation.
    report = compare.read_report(output / "results/inv")
    trial = report["pairs"][0]["candidate"]
    assert trial["observations"]["usage"] is None
    assert report["conditions"]["candidate"]["input_tokens"] is None
    assert report["conditions"]["candidate"]["output_tokens"] is None


def test_holdout_allows_exact_tie_but_rejects_equal_quality_at_higher_cost(tmp_path):
    _, _, output, _, _ = run(tmp_path, "worse")
    report = compare.read_report(output / "results/holdout")  # H0 vs H0.
    policy = SelectionPolicy(0, 0)
    assert holdout_accepts(report, policy)
    report["pairs"][0]["candidate"]["observations"]["usage"]["input_tokens"] += 1
    assert not holdout_accepts(report, policy)
