# Bounded skill evaluation

Skill evaluation measures discovery, correct application and task outcomes for an
exact knowledge revision. It is separate from the numeric engine-performance
evaluator and from InferRSI's L0–L4 system-validation ladder.

The first [real-client pilot report](../evidence/skill-eval-v1-20261021/README.md)
includes per-trial answers, retained development failures, discovery observations
and CPU-only replay of the frozen verifier. Its conclusions apply to that
selected task family, skill revisions and client/model configuration.
Its [order corrigendum](../evidence/skill-eval-v1-20261021/README.md#order-corrigendum--2026-10-22)
records the historical 4/2/0 first-position imbalance. Recorded elapsed differences
are observations with order/cache/provider-drift confounding, not causal skill
effects; a time-effect study needs a separately authorized frozen plan and budget.

The small report-audit driver reuses `KnowledgeReader` bundles, the real Codex
CLI and `harness.local_process.execute_command`. It adapts the sibling pilot's
fresh-session, event and independent-verifier methods; its old fixed
no-skill/human/distilled controller is not imported. No external evaluation
platform source is copied. Existing knowledge retains its source/license limits.

Ownership follows the module split. `inferrsi.harness.skill_evaluation` adapts the
client, executes each trial and retains its facts. `inferrsi.benchmark.skill_evaluation`
holds check validity, the task verdict and condition statistics.
`inferrsi.apps.skill_evaluation` composes both with the bundle export behind the
`run_trial`, `summarize` and `export_bundle` signatures; retained trial records are
unchanged.

## Conditions and outcomes

| Condition | Exposure | Purpose |
| --- | --- | --- |
| N | No target instructions or resources | Same-client baseline |
| E-benchmark / E-evidence | One explicit, complete reader bundle | Attribute each skill's effect separately |
| R | Both skills through native `.agents/skills` discovery | Observe relevant, ambiguous and irrelevant activation |

Future N/E schedules rotate by matched-group index alone; repeat numbers identify
pairs without changing the rotation. Every group contains all three conditions
once. Each consecutive block of three groups balances positions; across the six
`--final-only` groups each condition occupies each position twice. The ordinary
ten-group schedule has position counts of three or four. R probes follow these
groups. This fix leaves historical frozen schedules and outcomes unchanged.

Each trial starts a fresh ephemeral client and workspace. Global/bundled skills,
plugins, apps, memory and web are disabled for that client only. For this pilot,
the owner explicitly authorized relaxed host isolation after native Windows
isolation failed. Local commands have broad access: this is controlled knowledge
exposure, not OS-enforced read confinement. Keep known knowledge copies,
verifiers/answers and other trials outside the task workspace; list their roots
with `--protected-root` for the access audit. Invalidate observed exposure to them.
Unobserved/implicit exposure remains a limitation of the causal comparison.

Skill bundles keep their original relative paths and declared shared resources.
Other cross-skill navigation is not automatically made available. Skill identity,
content revision and resource/dependency revisions come from the existing reader.

Agent execution status and trial verdict are separate:

| Meaning | Example | Recorded trial verdict |
| --- | --- | --- |
| Input report verdict | A faster optimization fails quality | The correct answer says `report_verdict=FAIL` |
| Task correctness | The answer correctly rejects that report | `PASS`, with `evaluation_error=null` |
| Completed wrong answer | Valid JSON has incorrect fields, or is not an object | `FAIL`, with `evaluation_error=null` |
| Evaluation error | Timeout, missing/invalid JSON output, or unavailable/invalid grading | `INCONCLUSIVE`, with an `evaluation_error` category |

The verifier returns a boolean `passed` and a nonempty map of boolean
`assertions`; `passed` must equal their conjunction. Exit 0 accompanies a pass,
exit 1 a completed negative judgment. Other exits, missing/malformed results and
inconsistent result fields are evaluation errors. Invalid task latency inputs
(including empty groups, nonfinite aggregates or a nonpositive baseline) cannot
be graded as task success or failure. The pipeline retains execution and
verification records and saves the trial classification for these failure paths.

Grading checks structured verdict, scope, findings, numeric reduction and evidence
keys. Free-text `reason` is checked only for nonempty text; its meaning is not
judged. Audit rules retain their first-applicable precedence: workload mismatch,
kernel-only scope, missing quality, failed quality, failed requests, then gain
threshold. Extract mode only computes the descriptive reduction. The answer's
numeric tolerance remains 0.02 percentage points.

Condition summaries retain `PASS` / `FAIL` / `INCONCLUSIVE` counts and count
`evaluation_errors`; older records without that field keep their original
verdicts. Missing token/cost observations stay unknown, including partially
reported token metrics. Cached input is a subset of
input tokens, not an additional charge. Paired regressions and recoveries remain
visible, including neutral/negative results.
The controller's `observed_tokens` counts only known token fields and is a lower
bound when `token_usage_complete` is false; unknown totals remain null in the
summary. Invalid verifier payloads remain in their raw verification artifact;
only validated boolean checks enter the trial record.

## CPU checks and paid execution

After the normal development setup, with checkout `src` on `PYTHONPATH`:

```console
pytest tests/test_skill_evaluation.py tests/test_skill_eval_verifier.py tests/test_skill_eval_pipeline.py
python tools/skill_eval_pilot.py prepare --output <PRIVATE_OUTPUT> --model <EXACT_MODEL> --effort <EFFORT> --protected-root <KNOWLEDGE_AND_EVIDENCE_ROOT>
```

`prepare` writes a plan without model calls. Review and record its digest, budget
and revisions in the governing Issue before execution. Keep private output
outside the source checkout and audit accesses to the listed knowledge/history
and answer roots. Then the separately authorized call is:

```console
python tools/skill_eval_pilot.py execute --output <PRIVATE_OUTPUT>
```

Use `--max-new-trials 1` for the first real development trial. Reinvocation resumes
unattempted entries without retrying completed or incomplete calls. A client
execution failure stops scheduling until its cause is investigated. Campaign
time and observed-token limits carry across resumptions; the token threshold
stops new trials after recorded usage reaches it, rather than imposing a provider
generation limit. Unavailable billing cost stays unknown.

Default tests use deterministic fixtures; no CI model calls or new CI lane is
introduced. Fixture latency arrays reuse the historical Qwen review summary.
Counterfactual metadata is explicitly labeled as a test fixture, never as a new
GPU result. Two development instances and four sealed instances are selected
before calls. Do not tune skills or gates on the final held-out results.

The initial development campaign exposed a task-contract ambiguity: agents could
correctly reject a kernel-to-service claim while using FAIL where the hidden
oracle expected INCONCLUSIVE. Preserve those original calls and verdicts as
development diagnostics; they are not skill-harm evidence. Revised task inputs
state verdict and output-field semantics explicitly. `prepare --final-only`
selects four revised instances with a distinct historical baseline slice and
three R checks; two final instances repeat twice and two once (21 calls). Freeze
the revision and remaining `--campaign-seconds` / `--token-limit` before those
calls. Keep original and revised contracts separate and respect the Issue's
combined call cap; do not silently reinterpret old outputs.

The controller runs the verifier outside each task workspace and never executes
generated code. It checks structured answers, numeric results, findings and evidence keys.
Raw client captures remain private. Publish reviewed redacted evidence with its
own identity, per-trial outputs/checks, source revisions, missing observations,
resource definitions and a reproducible verifier invocation. A small pilot is
descriptive evidence for that skill/task/client/model, not automatic promotion or
a hardware performance claim.
