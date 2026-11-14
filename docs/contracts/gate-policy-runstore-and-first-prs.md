# Gate policy, RunStore, and first PRs

Status: **Proposed**. Author decisions under the t103/t108 delegation, offered for independent counterexample review. This document does not change the repository's current gate, owners, or review process, and it is not a claim that other maintainers accepted any part of it. Design review only; no implementation.

Covers D018/D009-r2 (first-wave gate policy), D012-r2 (RunStore), and D019 (first PRs). D023 is a bounded errata over these; where they differ, [`p0a-contract-errata.md`](p0a-contract-errata.md) wins and adds T36–T40.

The baseline the design is pinned to is `4b3afa55f278ca314e07d5f8acbd0a18f3fa72bf`: one evidence commit beyond the older `bd61d20`, with unchanged `src` paths. The 993-path inventory is path evidence only. The phase-one report came from worker static AST/source reading, **not** an Opus model conclusion: the original Opus call exited 1 with empty stdout/stderr, so it is neither a pass nor a safety refusal. Phase 2/3 Fable source audits were recovered as text. `pytest` was not run; the audit does not prove all 993 functions were covered, and the empty Opus failure cause was not established.

## A. Gate effectiveness and sequencing

The gate is not changed first. The sequence is: formally accept the exact gate policy, then migrate the seven K implementations, with old root names statically re-exporting the same implementation, and the catalog split per D020 into a neutral technical leaf. Pure single-module re-exports are not cross-core assembly; no Apps/B dependency or cycle is introduced for a mechanical alias. The first wave explicitly does not touch `evaluation`/`cli`/`arguments`/`main`, and does not change `harness.skill_evaluation` to import `knowledge` directly.

Rejected: relocating code before changing the gate; disguising root-file classification as Apps; dynamic-import or string-escape scanning; a blanket `harness/*` exception; two copies of K business logic; deleting old root imports; "migrate later, decide later".

## B. D009-r2 / D018: the first wave is exactly seven Knowledge root compatibility shells

### B1. The seven permitted edges

Relative to `src/inferrsi/`:

| old leaf | new implementation location / exact permitted target | compatibility requirement |
| --- | --- | --- |
| `catalog.py` | `inferrsi.knowledge.catalog` | K function bodies move to K `catalog`; `repository_pack`/`REPO_TYPES` move to the D020 neutral `_repo_pack_data`; root `catalog` statically re-exports both sources; `infra.inspection` changes only its same-leaf import, never via K/H |
| `_source_snapshot.py` | `inferrsi.knowledge._source_snapshot` | manifest/compact/resolve and existing source/hash identity unchanged |
| `skill_catalog.py` | `inferrsi.knowledge.skill_catalog` | `root:Path`/checkout physical resource semantics unchanged |
| `_skill_selection.py` | `inferrsi.knowledge._skill_selection` | `RELEVANCE_METHODS`/scoring functions/error types are the same objects; selection algorithm not rewritten |
| `research.py` | `inferrsi.knowledge.research` | `research_knowledge` and the existing public surface preserved |
| `knowledge_store.py` | `inferrsi.knowledge.knowledge_store` | `KnowledgeStore` type identity/CAS/exceptions unchanged |
| `knowledge_operations.py` | `inferrsi.knowledge.knowledge_operations` | Bundle/KnowledgeReader/RevisionConflict/IdentityError/safe_path/AUTHORITIES/IDENTITY and used symbols preserved |

`legacy-knowledge-manifest.json` lists the seven concrete modules, audited definitions/constants, and the extra symbols seen among consumers. Only explicit import targets and explicit `__all__` are allowed (the catalog's two neutral data-leaf names are listed per D020). Star imports, function/class/arbitrary statement exports, `__getattr__`, `sys.modules` substitution, and `importlib`/`__import__` escapes are prohibited. New-K internal references move to relative imports inside the same core and do not pass through the old root shells; other old modules may still use old root names, but every edge enters the transitive graph explicitly. Migration changes no business logic, only necessary internal imports.

The compatibility surface is not proven by saying "pure re-export": `old.Class is new.Class`, `old.func is new.func`, and old signatures/defaults/`Path`/exceptions are tested item by item. Existing module-level monkeypatch/global-replacement consumers that rely on the old module namespace affecting function `globals` may not be preserved by a pure re-export. Phase 3 confirmed seams D1–D8 exist; D020 has chosen to move the test patch targets, make private exports explicit, and handle lazy imports/relative references in the same PR. Existing supported behavior is not silently loosened. If an actual patch seam must be preserved, give a concrete compatibility design for the affected function/test before moving that leaf, and do not hide behind dynamic aliases.

The symbol inventory is AST-derived and does not claim to have found every third-party private use. Every confirmed module-defined symbol is preserved, and `_json`/`_hash`/`files`/`_source_snapshot` are already in the precise manifest route. The formal P0b review locks this manifest/hash; implementation cannot replace it with a wildcard.

### B2. Gate specification that must accompany the same review

1. The default cross-core prohibition stays. Only the seven leaf→exact-K-module edges above may serve as migration-compatibility exceptions; a module prefix does not authorize adjacent new modules.
2. Shape checks allow only a module docstring, a `future` import, the listed `ImportFrom` lines (alias only as `name as name`), and an `__all__` constant string list. No business definitions, other imports, assignment side effects, or dynamic module hooks.
3. The static graph resolves absolute/relative imports, `from package import name`, and known export edges, and builds the transitive closure. Any target K/B/H core API entry that reaches another core, Apps, or a legacy facade fails. The D020 neutral technical leaf is not a legacy facade and must be independently verified to contain only raw data reading/data constants and no core imports; "only indirectly, so it passes" is not allowed. Target H entry points are the new `harness.api`/`execution_service`/`run_store`; the current mixed `harness` does not automatically count as an isolated core.
4. The existing `harness/skill_evaluation.py → knowledge_operations → knowledge.*` edge is a registered **exact residual legacy dependency**: retained, and it keeps `overall isolation_complete=false`. It is not erased from the report and no new/expanded H bypass is permitted. It is not an exception available to a target H new entry. The skill-evaluation split (#410) later removed this edge and its exemption; [`../module-layout.md`](../module-layout.md#first-wave-compatibility-exceptions-proposed) records the current rule.
5. `test_clean_scan_is_not_isolation` becomes a staged-model test. It does not permanently assert that all three package directories are absent. It verifies that `overall` is still false when any target package, any compat exception, or an old H→model/infra transitive residual is missing. Green is not manufactured by deleting assertions.
6. A non-empty allowlist forces `overall isolation_complete=false`; "new core business closure isolated" is reported as a separate verifiable result and cannot impersonate isolation of the whole historical namespace.
7. New Apps entries call core public APIs only and never pass through old root shells; cores never import Apps. If a dynamic import is genuinely needed later, it must declare exact static edges and get extra review; in the first wave all compatibility shells prohibit it.
8. Every manifest item fixes old-file content hash, export name, target, consumer/retirement evidence, and tests. A new exception needs independent review; an unregistered edge fails by default in the first wave. After migration starts, only explicit changes from formal review update the manifest; no new H→Apps exception is slipped in under a "only shrink" slogan.

Synthetic negative controls run item by item per D020; an empty graph pass is not allowed on the grounds that target H files do not exist yet. Repository policy review is a process prerequisite, not another user choice. Without formal acceptance, these shells cannot be written and the gate cannot be changed.

P0b review package (Proposed): the exact pairs, shape rules, and staged isolation model are in [`../module-layout.md`](../module-layout.md#first-wave-compatibility-exceptions-proposed), [`../legacy-knowledge-manifest.json`](../legacy-knowledge-manifest.json), and `tests/test_target_module_import_gate.py`.

### B3. Later legacy evaluation entry (not mixed into the first wave)

The fixed later route: B `legacy_run_v0.py` keeps the only old `evaluate` rules; Apps `legacy/evaluation.py` adapts old RunSpec/args to B-local DTOs and returns the original dict; old root `evaluation`/`harness.evaluation` compatibility entries need per-leaf transitional policy review. The old protocol stays "INCONCLUSIVE if incomplete else FAIL if failed else PASS"; the reasons order `failed+incomplete` is not a verdict priority. Old run 0/1/2 are unchanged.

Before migrating that entry, **change the old runner so the execution core no longer calls legacy `evaluate` internally**: new H delivers only fixed execution facts, and evaluation is composed by the EvaluationPort that Apps injects; the old run composition path keeps the original call behavior in Apps legacy. Re-exporting old `harness.evaluation` is then a compatibility endpoint, not an H core dependency, and the gate must prove it is unreachable from every new H entry. This is not exemption by labelling business code `legacy`. While any shim remains in the old namespace, overall isolation is incomplete.

This route is chosen; Phase 2 gives the runner/evaluation cut points, and the concrete extraction and equivalence are accepted function by function in that later PR. It is an evaluation-migration start condition and does not block K01/H01 storage slices. No gate exception for those leaves is requested yet, and the current gate is not crossed early.

## C. D012-r2: no production RunStore exists; select a new local SQLite RunStore plus read-only fixed-byte resolution

Source fact: `src` has no RunStore; `MemoryArchive` and test `allocate_attempt` are not production capability. Therefore select a **new H-owned local RunStore v1**. It does not wrap a nonexistent system and does not treat an in-memory dict as a cross-process implementation. First phase is local filesystem only: no network disk, distributed transactions, daemon, or automatic takeover.

### C1. Namespace and storage layout

- Construction takes an explicit H RunStore root and a stable namespace id (a UUID by default, an explicit configured name allowed, see D021). Storage config and target config carry no credentials. Each RunStore root is an independent idempotent namespace; global exactly-once across roots is not claimed. `output_root` locates artifacts inside that namespace only and does not reallocate request execution authority.
- `index.sqlite3` stores attempt registration, immutable record payload raw bytes, and the latest version pointer. File artifacts are addressed by SHA256 at `objects/<sha-prefix>/<sha>`.
- `RecordRef.location` is `stores/<namespace>/attempts/<sha256(attempt_id UTF-8)>/records/<version>.json`; the configured read-only store reader resolves it from the SQLite record, so it is not implied to be a plain on-disk JSON file.
- Apps routes its ByteArchive and the H read-only store reader through an injected `ArtifactReader` (prefixes mutually exclusive, exact boundary match per D021; no match fails closed). B sees only the `open(ref)` protocol, does not import H, and does not create an execution service. Another process may open the same configuration read-only. Exporting fixed records and all associated bytes to an offline directory may also provide the same locator mapping. The reader never re-resolves latest.
- Blob/record bytes are immutable within retention. A location change requires an explicit reader configuration/export mapping; an old hash is never changed and nothing is fetched from the network. Existing material is not deleted without explicit retirement/cleanup authorization.

### C2. Transactions and failure protocol

| internal data/operation | the single design |
| --- | --- |
| attempts | `request_id` primary key, unique `attempt_id`, `request_digest`, `run_id`, `owner_session`, `state`, nullable `latest_record_version`; `request_id=attempt_id` |
| records | `(attempt_id, record_version)` primary key, canonical payload BLOB, `sha256`, `size_bytes`; append-only, old payloads never UPDATEd; versions increase monotonically and are never reused |
| claim | SQLite local transaction `BEGIN IMMEDIATE`; the unique constraint registers atomically; an existing key compares digest first, then returns by state; a failed registration transaction does not start |
| owner | non-blocking OS process lock plus registered `owner_session`; lock held and executing → `AttemptInProgress`; controller/process state unconfirmable → `RecoveryRequired`; a non-terminal registration with a free/unknown lock is always `RecoveryRequired` (even with no commit); no TTL auto-retry |
| before launch | only after identity/schema paths are legal, the claim is persisted, and budget/capability/resource and actual input binding pass may it start; no valid RecordRef is issued early |
| record commit | write known artifacts to temp files, flush/atomically publish to hash objects, verify, then commit the complete record BLOB and latest pointer in one transaction; issue a RecordRef only after the transaction succeeds and the bytes are readable |
| crash | object written but record uncommitted → unreferenced orphan bytes, no Ref, no automatic execution; record committed but response lost → same-key finished returns the original fixed record; state unknown → `RecoveryRequired` |
| commit failure | `started=false`: no start; `started=true`: best-effort cleanup, `PublicError.partial_record` uses a separate `PartialExecutionFacts` and does not impersonate a complete committed payload; `retained_refs` lists only genuinely verifiable old Refs |
| read | read only the latest committed version or a specified fixed version; no committed record → `NoCommittedRecord`; never assemble a fake fixed snapshot from attempt metadata |

A SQLite atomic transaction is not exactly-once for external side effects. Database busy/unwritable/disk-full must all be stably classified as `ExecutionPersistenceError` (with `started` per fact); lock contention never auto-creates a new attempt. The synchronous function does not wait for an existing owner to finish. Concrete OS lock adaptation and crash durability are test obligations, not an unselected product semantic.

The first phase does not force a public snapshot every phase; registration may be only persistent claim metadata, with the first terminal commit at `record_version=1`. If a genuinely committed `running` revision exists, D003 permits explicit offline assess; no checkpoint API is added for that eligibility. Later cleanup facts form a new version and do not overwrite the old one.

### C3. CPU execution and control domain

H02 implements one local supported-CPU process control domain: fixed order/fail-fast, timeout/CLI cancellation, and cleanup. It declares the process/resource domain it can actually control; POSIX process-group or Windows Job tests are not extrapolated into arbitrary escaping subprocesses or a remote launcher. A task requiring more than the real capability is refused before start. At runtime it does not parse/start arbitrary archived modules or pickles; supported entries are resolved by the local target adapter at the declared version.

Phase 2 gave the local process/runner cut points; concrete helper extraction and the Windows/POSIX control domain are measured item by item in the H02 implementation PR. The **new H execution interface/state/error/record is already fixed**; it is not waiting on the audit to choose policy. H01 storage may be implemented and accepted independently, and does not condition its own green light on H02 process-tree porting.

## D. D019: first-wave PRs and numbered acceptance

| PR | scope and explicit exclusions | dependency / formal gate | numbered acceptance (expected, not run) |
| --- | --- | --- | --- |
| PR-00a contracts | docs/schema/fixtures and the Decisions synthesized; no empty packages, no source moves | Fable review of this exact final version + normal repository review | A00-1 schema fields/semantics consistent with D023; A00-2 full golden bytes/projection positive and negative cases, including T36–T40; A00-3 old evaluate missing-item precedence and run 0/1/2 unchanged |
| PR-00b gate policy | seven-K leaf manifest, shape/transitive graph, staged isolation test, module-layout sync; no facade implementation | normal architecture review explicitly accepts the exact compatibility exceptions; without acceptance no move | A01-1 seven exact allowances accepted / adjacent modules rejected; A01-2 function/dynamic hook/star import rejected; A01-3 H→facade→K bypass detected; A01-4 three packages may appear in stages but a non-empty allowlist/residual keeps overall false |
| PR-K01 seven K modules | seven K bodies move per D020 into `knowledge` names (catalog sheds the neutral data leaf); old roots are pure static exports, one `infra` import and D1–D8 tests move together, lightweight `init`; new K uses same-core references; **no data, `pack.json`, CLI, evaluation, or plugin layout moves; Bundle algorithm unchanged; `files("inferrsi")` anchor fixed** | PR-00b formally accepted and merged; D020's existing patch/private-name/lazy-import contracts are implemented and verified together; not waiting for a whole-repo function inventory | K01-1 old/new type/functions are the same objects, signatures/exceptions/Path behavior; K01-2 old root consumers equal new K results; K01-3 existing Bundle/revision/CAS/pins/source-attribution fixtures unchanged; K01-4 installed wheel queries the package outside checkout, `repository_pack`/`infra` inspection and explicit checkout `Path`; K01-5 overall false, no new direct H→K; K01-6 packaging still includes old resources, plugin physical runtime layout unchanged |
| PR-H01 persistent record core | new H run_store/input/record byte read-write and atomic claim; real capability does not depend on `MemoryArchive`; no Benchmark/Apps/Agent wiring; no claim of a full execute CPU flow | PR-00a formally accepted; does not need K01 or H02 done, and requests no H→Apps/B exception | H01-1 multi-process same key yields exactly one claim; H01-2 different digest conflicts; H01-3 after commit a new process reads the fixed record/hash/version; H01-4 every transaction/object-write crash point yields no fake Ref, including external side effect before commit and free lock still `RecoveryRequired`; H01-5 old Refs never overwritten, `NoCommittedRecord` honest; H01-6 new H closure reaches neither legacy/K/B/Apps |
| PR-H02 single-CPU execute | extract the audited process-execution/input-retention capability into the new H core; public `allocate`/`execute`/`read`; ordinary command mode is enough; evaluation-mode fields, including `expected_code_sha256`, remain null. This slice does not implement T37. | H01; concrete process helper/existing-behavior audit complete; normal review | H02-1 a real CPU exit 0 is still not PASS; H02-2 timeout/cancel/process-domain cleanup recorded separately; H02-3 actual candidate/input identity mismatch (T25/V28) yields a fixed `not_started` record, which is not T37 entry-code digest mismatch; H02-4 re-entry/unknown owner/finished replay starts nothing new; H02-5 three budget facts/lease release/persistence failure start state unique; H02-6 no device/model/network fallback |
| PR-K02 physical resource migration | fixed knowledge assets → `knowledge/data`; `pack.json` is not moved with them; the resolver is compatible with old logical pins and physical aliases, handled per source | K01; Phase 2/3 named resource evidence complete; K02 resolver/packaging artifact verified together | K02-1 verify importlib / checkout `Path` / plugin physical paths separately; K02-2 content/source_revision/dependency pins unchanged per original rules or explicitly re-revisioned; K02-3 real wheel/affected host products read resources outside the package |
| PR-B01/Apps later | B prepare/assess/worker fixed closure, Apps assembly, legacy evaluation migrated entry by entry | each requires its interfaces available and independent review; blocks neither K01 nor H01 | T01–T40; legacy v0 semantics retained, new scopes independent; no unrelated real-Agent/multi-host prerequisite |

Rollback: 00a/00b withdraw the corresponding document/test-contract changes rather than skipping checks; K01 restores the old implementation and internal imports as one slice (the old resources were never moved); H01 stops new writes but keeps committed DB/objects and readable old versions, and never deletes audit material for rollback; H02, on unknown external state, stops new starts rather than rerunning an old attempt through rollback. The user authorized starting migration after convergence, but this round is design and review only; these PRs are not actually landed here.

## E. What is still genuinely owed (kept narrow)

- Before K01: D020 has locked D1–D8 and the actual private names/import cycles from Phase 3; the implementation PR verifies old-entry coverage and new-implementation injection and does not claim exhaustive external private use. This is not a demand to review all 993 files' functions.
- Before H02: the exact function edges and compatible behavior of the existing process/WindowsJob/local-executor/input-materialization/record helpers; the lifecycle policy is not re-chosen.
- After H02, before evaluation-mode execute: D023-2/V32/T37 pre-start entry-code digest check. Acceptance **T37-N1** (expected already equals the frozen recipe; actual entry digest differs → zero start, submit `not_started` with mismatch facts, or `ExecutionPersistenceError` if that submit fails) and **T37-N2** (unresolvable entry → `not_performed`/unknown, `RuntimeBinding.code_sha256` stays null, no fabricated mismatch). Ordinary-command null mode (H02) does not cover these outcomes. This is specified in P0a; implementation is a later H evaluation-mode binding/execute slice, not an expansion of the H02 acceptance list.
- Before K02: the full Bundle/source_revision/registry generator and the three-class resource loading/packaging path.
- Before Apps/events later: the actual accepted constraints/fields of #333 and #371, and the exact call point where the old runner decouples from `evaluate`. D014 keeps the first-phase event as an H fact-read-model extension only; #333/#371 are explicitly Proposed, D022 chooses the new Apps mapping, and no claim is made that #333 is accepted or that the unified event protocol changes immediately. This does not block K01/H01.

Beyond this, real Agents, remote/multi-branch, new quality metrics, and all unknown third-party private uses are not unlimited prerequisites for this first wave, nor do they excuse ignoring confirmed supported behavior. The design choice is delegated to the author, who will not ask the user to decide each item.
