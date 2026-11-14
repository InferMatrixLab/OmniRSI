# Target module layout

This page records the directory rule for the integration slice. The first-wave
Knowledge move (K01) is implemented: `src/inferrsi/knowledge/` holds the seven
moved Knowledge bodies, and the old root modules are static compatibility
shells. The engineering task contribution now implements `benchmark/registry.py`
and `benchmark/tasks/`. The current flat `cli.py` and `benchmark_operations.py`
assemble this slice.
#371 remains the proposal for the Apps package path and is not accepted here.

## Target modules

The four target modules are peers: `knowledge`, `harness`, `benchmark`, and
`apps`. Knowledge, Harness, and Benchmark do not import one another. Apps is
the only assembler. `src/inferrsi/contracts.py` stays a `RunSpec`
re-export. It must not become a shared business-contracts package.

The current `src/inferrsi/harness/` tree is a mixed implementation. Its
presence is not the target Harness boundary and is not evidence that the four
modules are isolated.

There is no sibling `data/` resource tier. K02 moved the Knowledge corpus to
`src/inferrsi/knowledge/data/` and the `repo_packs/*/pack.json` target
descriptions to `src/inferrsi/harness/data/`. The internal corpus split is left
to a future Knowledge issue. `observability/template.html` is a view template
and follows the Apps slice; it did not belong to the corpus merely because it
sat under `data/` before.

The move is physical only. Bundle resource keys, dependency keys and the pinned
`source_files` paths in generated records keep their recorded spelling, so
content bytes, hashes and Bundle revisions are unchanged. `_source_snapshot.LIBRARY`
names the corpus at its pinned `source_revision`, not the current checkout path;
repointing it regenerates every record and needs an explicit new revision.

`src/` stays the import root. `tests/` and `tools/` stay at the repository
root. Also keep these root files where they are: `AGENTS.md` (entry contract),
`CHANGELOG.md`, `MANIFEST.in` (it grafts `src/inferrsi`, so a later resource
move must update packaging in that same slice), and `.agents/` (review skill).

Do not create empty `knowledge/`, `benchmark/`, or `apps/` directories in the
contract slice. Do not treat PR #338 as a reason to add a top-level
`benchmarks/` tree.

## CPU/offline connection

`tests/architecture_closed_loop.py` is the handoff exercised by
`tests/test_architecture_closed_loop.py`. The coordinator does not import
Knowledge, Harness, or Benchmark. Refusal cases still inject outcomes. That
file is not the Apps package, and #371 remains the proposal for that path.
It does not change Bundle revision.

`tests/architecture_real_ports.py` is the CPU/offline connection injected for
the success path. It calls `KnowledgeReader.read` on one installed package
card, `execute_trial` for one local process record, and `evaluate` for one
offline assessment. It does not call `collect` or `search_knowledge`. It does
not choose an archive owner or a run store. `MemoryArchive` still stores only
prepared and assessment bytes. The module stays under `tests/`. A process
exit of 0 in that record remains an execution fact; this assessment does not
turn it into a campaign `PASS`.

## Tentative current ownership

This table is a reading aid for the import gate. It does not authorize moves.
Several flat modules remain shared seams until their slice starts.

| Current path | Tentative reading | This slice |
| --- | --- | --- |
| `cli.py`, `arguments.py`, `__main__.py` | Future Apps entry, after #371 | Not moved |
| `benchmark_operations.py` | Single-task application launcher | Resolves a task entry and invokes the Harness runner; ordinary projects do not use it |
| `benchmark/registry.py`, `benchmark/tasks/` | Task discovery, definitions and specialized rules | Task-specific quality and measurement code stays with each task |
| `harness/optimization_session.py`, `harness/project.py`, `harness/task_protocol.py` | Shared preparation, execution and retained-result assessment | CLI supplies task resources and knowledge access; ordinary project lifecycle does not import Benchmark |
| `harness/optimization.py`, `harness/candidates.py`, `harness/usage.py` | Shared project and packaged-task lifecycle | Injected execution/assessment; declared file/directory candidates, selection direction and exact/up-to rounds |
| `harness/agent_sessions.py` | Native host handoff records | Freezes requests and retains native IDs and exported observations; the initiating Agent calls host tools |
| `harness/skill_evaluation.py`, `harness/artifact_sandbox.py`, `harness/improvement.py`, `harness/atomic_json.py` | Skill-evaluation host adapter, trial execution and retention, sandbox replay, search progression | Verdict rules and task definitions arrive as injected values; no Model, Infra, Knowledge, Benchmark or Apps import |
| `benchmark/skill_evaluation.py`, `benchmark/skill_eval_diagnostic.py`, `benchmark/skill_eval_engineering.py`, `benchmark/improvement.py` | Skill-evaluation criteria, task definitions and acceptance rules | Pure judgments over retained facts; no Harness import and no process start |
| `apps/skill_evaluation.py`, `apps/skill_eval_engineering.py`, `apps/improvement.py` | Composed skill-evaluation entries | Join Knowledge reads, Harness execution and Benchmark rules behind the signatures that `harness/` held before the split |
| `knowledge_operations.py`, `knowledge_store.py`, `catalog.py`, `research.py`, `skill_catalog.py`, `_skill_selection.py`, `_source_snapshot.py` | Static compatibility shells since K01; bodies in `knowledge/` | Interfaces unchanged |
| `harness/` | Mixed legacy tree, not target isolation | Not redefined |
| `evaluation.py`, `model/validators/` | Benchmark-leaning checks | Not moved |
| `infra/`, `execution.py`, `runspec.py`, `inspection.py` | Seams used by the current tree | Not a fourth core |
| `reporting.py`, `summary_chart.py`, `evidence_paths.py` | Current report helpers | Not moved |
| `claim.py` | Verdict vocabulary used by current checks | Not moved and not a fourth core |
| `contracts.py` | `RunSpec` compatibility re-export | Must remain one module file |
| `knowledge/data/`, `harness/data/` | Packaged resources, placed by owner since K02 | Corpus under Knowledge; pack descriptions under Harness |

## Import gate

`tests/test_target_module_import_gate.py` fails on a new import of
`inferrsi.knowledge`, `inferrsi.benchmark`, or `inferrsi.apps` from anywhere
except application assembly, and on Knowledge or Benchmark importing Harness.
Application assembly includes `inferrsi.apps` and the two current flat entries
`cli.py` and `benchmark_operations.py`. Other flat modules retain the same
restrictions. The gate checks Benchmark task scripts as well as module code.
`from inferrsi import knowledge` counts as an import of that core.
Same-core imports are allowed, including a relative import rewritten to an
absolute name such as `from .bar import VALUE` inside `knowledge/`. Each forbidden
importer and target pair is reported once. The only exceptions are the exact
first-wave pairs below, read from `docs/legacy-knowledge-manifest.json`.

The Benchmark package now provides resource discovery and task scripts. The
CLI and single-task application launcher assemble them with Harness; task
scripts receive execution support without importing another core.
"There is no exception list of current edges" described the tree before K01,
when the three package names were unused and no current edge needed an
exception. It was never a permanent ban on reviewed exceptions; since K01 the
exact first-wave Knowledge pairs below are the only Knowledge compatibility
exceptions, and they remain scoped to that move.

The gate scans `src/inferrsi/**` as the transfer graph. It resolves absolute,
relative, and `from package import name` imports, includes the parent-package
`__init__` that Python executes when a submodule is imported, both for import
targets and for the imported module itself (except the root `inferrsi`
package init, which runs for every import and is not a business edge), adds
the manifest-declared export edges, and computes the transitive closure. Since
K02 that scan also reaches the packaged helper scripts inside
`knowledge/data/**`, which are resources rather than importable modules: their
directories have no `__init__.py`. They import only the standard library and
external engines, so the Knowledge closure rule holds for them unchanged. K02
did not change the gate's scan scope or any closure rule. A dynamic import (`__import__`, `importlib.import_module`, or
`module_from_spec`) inside a checked closure is an undeclared edge and fails
closed, except for the exact resource execution declarations below.
Other dynamic forms (`getattr`, `exec`, `eval`, `runpy`, `pkgutil`)
are out of scope for this package. `tools/` and `tests/` are separate
registered consumers; this gate does not scan them.

The task integration declares these existing resource execution boundaries in
`tests/_gate_policy_support.py`, independently of the Knowledge compatibility
pairs. Each permits exactly one `importlib.util.module_from_spec` call, with the
listed local spec variable, in the named function or module scope. Other dynamic
calls, additional calls, adjacent modules and static cross-core imports remain
checked. These declarations do not make arbitrary candidate code isolated.

| Relative module | Scope | Local spec |
| --- | --- | --- |
| `harness.task_protocol` | `protocol` | `spec` |
| `benchmark.tasks.moe-forward-npu.benchmark` | module | `_process_spec` |
| `benchmark.tasks.moe-forward-npu.grader` | `load` | `spec` |
| `benchmark.tasks.qwen21-denoise-npu.benchmark` | `load` | `spec` |
| `benchmark.tasks.qwen3-tts-npu.benchmark` | module | `_evaluation_spec` |

The report adds a `runtime resource loaders` blocker when these loaders are
present and does not claim either complete isolation or new-core closure
isolation. The seven Knowledge shells and their nine exact pairs are unchanged.

A clean result means no new cross-core import of those names. It does not mean
the modules are isolated. Isolation stays false while any one of these holds:
`knowledge/`, `benchmark/`, or `apps/` is absent or has no scannable
module; the compatibility allowlist
is non-empty; or `harness/**` still imports Model or Infra,
directly or through another module. The three packages may appear in stages.
A clean graph for the new cores is reported separately as "new core business
closure isolated" and never sets overall `isolation_complete` to true.

`knowledge-model-policy` stays the only required status. Until a separate
process decision makes this import gate required, every migration PR attaches
the gate result and does not describe it as branch protection.

## First-wave compatibility exceptions (Proposed)

These exceptions implement D018/D020 in
[`contracts/gate-policy-runstore-and-first-prs.md`](contracts/gate-policy-runstore-and-first-prs.md)
and
[`contracts/isolation-errors-and-cli.md`](contracts/isolation-errors-and-cli.md).
They stay Proposed until this policy passes formal architecture review. K01
must not start before that review accepts them.

Relative to `src/inferrsi/`, exactly nine (importer, target) pairs are
permitted:

| Importer | Exact target | Route |
| --- | --- | --- |
| `catalog` | `inferrsi.knowledge.catalog` | Named symbols only; not `repository_pack` |
| `catalog` | `inferrsi._repo_pack_data` | `REPO_TYPES`, `repository_pack` |
| `_source_snapshot` | `inferrsi.knowledge._source_snapshot` | Same-named Knowledge module |
| `skill_catalog` | `inferrsi.knowledge.skill_catalog` | Same-named Knowledge module |
| `_skill_selection` | `inferrsi.knowledge._skill_selection` | Same-named Knowledge module |
| `research` | `inferrsi.knowledge.research` | Same-named Knowledge module |
| `knowledge_store` | `inferrsi.knowledge.knowledge_store` | Same-named Knowledge module |
| `knowledge_operations` | `inferrsi.knowledge.knowledge_operations` | Same-named Knowledge module |
| `infra.inspection` | `inferrsi._repo_pack_data` | Direct, never through `catalog` or H |

These nine pairs are the only compatibility exceptions. A module prefix does
not authorize an adjacent module, and a second importer of an allowed
Knowledge target fails. `inferrsi._repo_pack_data` has exactly two permitted
importers outside Knowledge: root `catalog` and `infra.inspection`. An import
of that leaf from inside `inferrsi.knowledge`, for example from
`inferrsi/knowledge/catalog.py`, is intra-closure and not a tenth exception.
The leaf's own import rule and `REPO_TYPES` object identity govern it.
`REPO_TYPES` is the same tuple object through root `catalog`, the leaf, and
`inferrsi.knowledge.catalog`.

- **Shell shape.** An old root leaf that imports its Knowledge target or the
  data leaf is a compatibility shell. It may contain only a module docstring,
  a `__future__` import, `ImportFrom` lines from its listed targets with
  listed names (an alias only as `name as name`), and an `__all__` that is a
  literal list of constant strings. Star imports, function or class bodies,
  module-level assignment, `__getattr__`, `sys.modules`, dynamic imports, and
  unlisted import lines or names fail. An old leaf that keeps a body after its
  Knowledge module exists also fails.
- **Data leaf.** `inferrsi._repo_pack_data` imports only `json`,
  `importlib.resources`, `typing.Any`, and `from __future__ import
  annotations`, anywhere in the file. It defines only `REPO_TYPES` and
  `repository_pack`. Any `inferrsi.*` import or dynamic import in it fails.
- **Closures.** A Knowledge module reaches nothing outside Knowledge except
  the data leaf and `inferrsi._json_v1`, the canonical encoder in the P0a
  design. Benchmark modules and every `inferrsi.harness.*` module reach no
  other core, no Apps module, and no old root leaf. Apps reaches no old root
  leaf. `inferrsi.infra`
  reaches no Knowledge, Harness, Benchmark, or Apps module, so `infra` → root
  `catalog` → Knowledge fails while `infra` → the data leaf passes.
- **New H entries.** The registered entry modules are
  `inferrsi.harness.api`, `inferrsi.harness.execution_service`,
  `inferrsi.harness.input_binding`, `inferrsi.harness.local_process`,
  `inferrsi.harness.run_store`, and `inferrsi.harness.errors`. Each entry's
  closure is walked on the full graph. The gate does not infer other module
  paths from labels. Any `inferrsi.harness.*` module that exists is still
  checked against the closure rule above. The current mixed `harness` tree is
  not automatically an isolated core.
- **No residual.** The skill-evaluation split (#410) moved `export_bundle` to
  Apps and removed the registered `harness.skill_evaluation` → root
  `knowledge_operations` edge together with its exemption. Every harness
  module is walked on the full graph. A harness module that reaches an old
  root leaf fails, and so does each harness module that reaches the leaf
  through it. The report lists every remaining `harness` → Model or Infra
  path in `harness_model_infra`; the split modules `harness.skill_evaluation`,
  `harness.improvement`, `harness.artifact_sandbox` and `harness.atomic_json`
  have none.

The manifest records the base commit, each old leaf's file hash at that
commit, export names, audited definitions, consumer evidence, catalog's two
symbol routes, the required `infra.inspection` import rewrite, and the
original `REPO_TYPES` / `repository_pack` fragment from `catalog.py` with its
hash. K01 created `_repo_pack_data.py`, and the manifest locks its full-file
hash. K01 re-verified every recorded hash against its own base. Two leaves
had changed after P0b, so K01 re-recorded all seven hashes at its base and
keeps the P0b values in `revision_history`; that revision goes through formal
review with the move.

K02 moved `repo_packs/*/pack.json` to the Harness resource root, so the leaf's
one path expression and its locked `file_sha256` changed. The manifest records
that as `technical_leaf.resource_path_revision` with the old and new hashes and
the exact old and new line, keeps the P0b `original_fragment` for provenance,
and adds `current_fragment` for the body in the file today. The importlib anchor,
allowed imports, two symbols, top-level shape and the two permitted importers
are unchanged, so `infra` still gains no edge into Harness. That manifest
revision goes through formal architecture review with the K02 move; the
implementer does not self-approve it.

## Old `run` exit codes

Frozen from
[`src/inferrsi/cli.py`](https://github.com/InferMatrixLab/InferRSI/blob/9f61a83f030bfa2ffe3b54eac1781593309d6380/src/inferrsi/cli.py#L412-L417)
as the compatibility text for a later integration check. This page does not
change that code.

| Condition | Exit |
| --- | --- |
| `status["verdict"]` is `PASS` | 0 |
| `status["verdict"]` is `FAIL` | 1 |
| Any other verdict, including missing | 2 |
| `OSError`, `ValueError`, `JSONDecodeError`, or `sqlite3.Error` via `parser.error` | 2 |

## Fixture locations

`tests/fixtures/skill-comparison/` and `tests/fixtures/skill-eval-pr/` hold
the skill-eval fixtures. `tests/fixtures/` also contains helpers such as
`skill_eval_pr_support.py` and `skill_eval_compare_support.py`. Do not
overwrite those helper modules. A later path change must update the tests,
docs, and workflow that cite these directories in the same change.
