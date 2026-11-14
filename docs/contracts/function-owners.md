# D008-r2 — explicit ownership for reviewed functions

Status: **Proposed**. Author decisions under the user's delegation, based on the same-SHA Phase 2/3 source text. Audit-model suggestions are not automatically treated as accepted. Target directories and names are design; this does not claim the production code already exists, and unreviewed functions are not moved merely because of their file name. This table resolves owner choices raised in discussion; it does not make later B/Apps/Agent implementation a prerequisite for P0a/K01/H01.

| Existing file / symbol | Chosen owner and target responsibility | Migration / retention constraint |
| --- | --- | --- |
| `harness/skill_evaluation`: `CodexClient`/`global_skills`/`client_config`/`toml_value`; the execution part of `run_trial` | H agents/execution-side host adaptation | argv/host config/execution/retention belong to H; if `run_trial` calls scoring/statistics/export internally, those must be split to B/Apps and composed through injected ports; the original mixed function is not relabelled as pure H as a whole |
| same file `trace_metrics`/`normalize_usage` | H capture fact parsing and normalized `UsageObservation` | **Do not adopt the Phase 2 suggestion to place `normalize_usage` in B**; B consumes local usage DTOs converted by Apps, does not import H, and does not parse provider events |
| same file `valid_checks`/`task_verdict` | B fixed criteria/legacy rules | kept separate from execution facts; old `evaluate` behavior compared unchanged |
| same file `summarize` | B statistics/comparison eligibility; Apps presentation only | business statistics are not migrated wholesale into presentation; B is still not allowed to call H |
| same file `export_bundle` | Apps composing K reads + H fixed retention | H execution core never imports K; export hands over actual bytes only |
| `skill_eval_diagnostic` task definition/`validate_task`/`expected`/`verify` | B task/pure criteria; `main` as a B-defined worker entry | worker started by H; prepare/assess do not execute `main` |
| `skill_eval_engineering` task/vectors/`validate_task` | B task-owned definition and pure vector judgment | `verify` must be split from process start; offline assess does not re-measure through an execution port |
| same file `materialize_generated_artifact`/`read_regular`/`input_hashes`/`SandboxUnavailable`/`RUNNER`/`sandbox_command`/`_invoke` | H input capture/sandbox execution capability | real capability/control domain tested in the later H02 PR; B consumes fixed output only |
| `harness/runner` execution/termination/directory/state events | H execution and facts; AB/BA/repetitions → B protocol/worker; `write_report` → Apps | old run unchanged; migrating old `evaluate` decouples the runner first; new H scoring cannot be retained by attaching a `legacy` label |
| `harness/improvement` `usage_totals`/`assessment`/`holdout_accepts`/`rerun_assessment` | B experiment validity/comparison/acceptance rules; budget admission/reservation/charging reconciliation remains H | read normalized facts only; provider parsing does not move because of import convenience |
| same file `choose` | H search progression; `SelectionPolicy` split into B threshold rules and H progression/stop policy | no shared business `Policy` class; Apps does not create a retry loop |
| `harness/knowledge_capture` `assemble_context_text`/`_item` | K pure knowledge text projection | Apps connects K/H; Apps gives H the fixed expected text/hash; H stores the actually delivered text/hash; `verify_capture` byte integrity belongs to H and does not call K assembly in reverse; it is not a task-benefit verdict |
| `harness/scenarios/external` `read_metric`/`validate_metric` | B semantic validation; `execute_command` and retention → H | no whole-file ownership; offline assess executes nothing |
| `campaign_records` `STAGES`/claim capture/reconciliation | H records; dashboard/report presentation → Apps | first remove the `campaign_records → dashboard_data` reverse dependency; #333 is Proposed and no claim is made that a unified event envelope is accepted |
| `catalog.repository_pack`/`REPO_TYPES` | D020 neutral raw-data technical leaf; target interpretation stays H | K does not expose the pack reader publicly; `infra` changes only its same-leaf import; seven K bodies/root entries/tests move together |

Any later split of a mixed function is accepted by real pytest/host/wheel/record comparison in the corresponding PR; none of this delays today's contract or gate-policy start. Existing external public composed entries keep their original semantics and paths per D018/D022, and an owner change cannot be used to delete an old promise.

## Implemented rows

The skill-evaluation split (#410) implements the `skill_evaluation`, `skill_eval_diagnostic`, `skill_eval_engineering` and `improvement` rows. H keeps `harness/skill_evaluation.py`, `harness/artifact_sandbox.py` and `harness/improvement.py`; B holds `benchmark/skill_evaluation.py`, `benchmark/skill_eval_diagnostic.py`, `benchmark/skill_eval_engineering.py` and `benchmark/improvement.py`; Apps composes them in `apps/skill_evaluation.py`, `apps/skill_eval_engineering.py` and `apps/improvement.py`. Two points follow the tree as it stands after #338, not the row text:

- `usage_totals` stays in `harness/usage.py`, because `harness/optimization.py` consumes it for budget accounting. B `assessment` receives the baseline and candidate totals from Apps and does not import H.
- `SelectionPolicy` holds only the two thresholds, so it lives whole in B. H `choose` receives the assessments, the validity block and the composition flag; it holds no threshold and no policy class.
