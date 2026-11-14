# Current design: K / H / B + Apps contracts v1

Status: **Proposed**. See [README.md](README.md) for repository-wide status and dependencies.

This file is the design overview: D001–D019, the boundary and port model, and where each normative decision lives. Where D023 and this file differ, `p0a-contract-errata.md` wins. Machine fields are authoritative in `schemas/contract-v1.schema.json`; this prose cannot widen or narrow them.

## Normative set and reading order

| File | Covers |
| --- | --- |
| [`current-design.md`](current-design.md) | this overview; D001, D003–D006, D010, D011; pointers to D002, D007–D009, D012–D019 |
| [`schema-and-semantics.md`](schema-and-semantics.md) | D002/D017: canonical `inferrsi-json-v1` encoding, identity projections, V01–V35 |
| [`isolation-errors-and-cli.md`](isolation-errors-and-cli.md) | D020 Knowledge split, D021 storage routing/errors, D022 Apps request/result matrix |
| [`p0a-contract-errata.md`](p0a-contract-errata.md) | P0a errata: prepare closure bytes, frozen code digest, deliver-then-seal context, claim-key order, CLI attempt save/replay; T36–T40 |
| [`gate-policy-runstore-and-first-prs.md`](gate-policy-runstore-and-first-prs.md) | D009/D018 first-wave gate policy, D012-r2 RunStore, D019 first PRs |
| [`function-owners.md`](function-owners.md) | D008 function ownership for the audited mixed modules |
| [`acceptance-criteria.md`](acceptance-criteria.md) | T01–T40 unique outcomes |
| [`figures.md`](figures.md) | the eight figures |
| [`schemas/contract-v1.schema.json`](schemas/contract-v1.schema.json) | Draft 2020-12 definition library, 106 named `$defs` |
| [`schemas/INDEX.json`](schemas/INDEX.json), [`dto-field-index.md`](dto-field-index.md) | machine-derived indexes; the schema wins on conflict |
| [`fixtures/README.md`](fixtures/README.md) | historic baseline bytes vs current D023 vectors |
| [`README.md`](README.md) | index and status |

Historical candidate text, review dispositions, and the earlier Proposed drafts are evidence only. They are not a second live rule set. The 993-path inventory is path evidence, not a function-level audit, and does not itself authorize any first-wave change.

## D001 — One distribution, three cores plus Apps, document contracts rather than shared business DTOs

Decision: keep Knowledge, Harness, and Benchmark as sibling independent cores; Apps is the only assembler. Each core owns its types. Cross-boundary traffic uses versioned JSON envelopes with explicit conversion; there is no shared business `contracts` package. `contracts.py` remains a single-file `RunSpec` re-export. The legacy `claim.py` enum stays for compatibility but is not a new shared business model.

Permitted shared technical leaves are only:

- `inferrsi._json_v1` — the canonical encoder (D002);
- `inferrsi._repo_pack_data` — the neutral raw pack-data reader and the existing static data enumeration (D020).

The neutral leaf carries no target selection, task, scoring, scheduling, or public business DTO, and imports none of K/H/B/Apps.

Why: this matches the accepted A–D direction and removes the reverse dependency that a shared DTO package creates. Rejected: a shared business DTO package; Apps owning scoring/sampling/budget algorithms; creating empty packages to declare migration done.

Acceptance: no core's public surface exposes another core's Python class; the transitive dependency graph of a core has no back edge to another core or to Apps; installing the ordinary distribution allows querying K without loading device or agent capability. Repository review is still required; delegation does not auto-approve.

## D003 — C2: explicit offline assess of a committed, fixed non-terminal snapshot

Decision: C2-A. A legal fixed snapshot may be explicitly assessed offline once top-level identity and version checks pass; phase is not used as a blanket rejection. Synchronous admission refusal, re-entry anomalies, and pending-recovery states still stop automatic progression. Only historically existing committed revisions qualify; no checkpoint/submit API is added and no phase is required to commit.

A completed-measurement claim must satisfy the completion/sample/cleanup requirements frozen at prepare. `not_started` and legacy `running` are not successful measurements. Independent format/correctness claims may FAIL on trustworthy error proof. Missing or conflicting evidence without a task-specific rule makes that claim INCONCLUSIVE. Transient observations and uncommitted partial facts cannot enter a fixed determination. Unknown cleanup cannot override an independently proven candidate error.

Why: the fact axis and the judgment axis are independent. Rejected: rejecting every `phase != finished`; `finished` meaning automatic PASS; the UI inferring evidence that was never captured.

Acceptance: T04–T07, T11, T23; cross-process offline assess performs no subprocess, network, or device call.

## D004 — scope and claim are independent; Apps performs no semantic derivation

Decision: results are stored by `(claim_id, validation_scope)`; different scopes do not overwrite each other. Without a frozen aggregation protocol there is no overall verdict; the CLI summarizes only the primary scope the request explicitly names. Scope matching uses the exact canonical identifier/encoding given by B's public protocol. Containment, equivalence, and mapping come only from B's public rule; Apps never derives them.

A same-claim/same-scope conflict defaults to INCONCLUSIVE; last-write-wins is not used. Any rule allowing independent error proof must be frozen before prepare. When a strong independent FAIL coexists with other missing items, the claim's frozen rule decides; Apps does not invent a priority. Legacy run v0 aggregate behavior is preserved as a separate versioned legacy protocol (D009) and is not presented as the new overall result.

Acceptance: T02, T08–T10; an error in one scope must not change another. Rejected: "overall failure covers all scopes"; Apps treating "format passed" as correctness or measurement passed.

## D005 — worker definition, execution, and the prepared resource closure

Decision: B owns the fixed worker recipe/entry revision, roles, sampling (AB/BA), repetition, timing, and aggregation; `prepare`/`assess` never start a worker. H schedules the outer declared steps and owns lifecycle, deadlines, capability, budget, and cleanup; the optimization loop and stop rules stay in H. Apps maps roles, fields, and versions verbatim and builds no second loop or retry policy.

The recipe participates in `prepared_id` and is archived immutably. Fixed resources required by execute map, by frozen role and artifact sha/size/media, to `ExecutionSpec.InputSpec` and the actual `BoundInput`; a same-named caller input cannot override or rebind them. H verifies only fixed identity and actual bytes and does not interpret task scoring; B re-checks that the evidence used in the run belongs to that frozen resource closure. `materialized_path` remains an explicit spec field, and relocating a path is not a modification criterion. Protocol, task assets, and the resources required for offline assess form the closure by fixed role/hash/size/format. Supported externally installed code binds by fixed version/capability; the interpreter is not archived wholesale and arbitrary archived modules or pickles are not executed, and missing pieces are not fetched from "latest".

Outcomes: missing capability for execution → `not_started`; unsupported assessor version → explicit refusal; missing single item inside an otherwise correct combination → the affected claim has missing evidence.

Acceptance: T15–T17, T21; relocating resources does not change logical roles or pins. Rejected: recipe as a mutable side attachment; H importing B to find its worker; Apps doing its own sampling.

## D006 — identity, errors, lifecycle, and unique return on re-entry

Decision: keep the v0.3 unique-return table, completed here for the prose and figures.

| Input situation | Unique public result | Start / new Ref |
| --- | --- | --- |
| schema/path invalid and not registered | `InvalidExecutionSpec` with field errors | no start; no started record fabricated |
| first legal request, registration fails | `ExecutionPersistenceError(started=false, partial_record=null)` | no start; no new RecordRef |
| same `request_id`, different `request_digest` | `RequestConflict`; original request preserved | no start; no overwrite |
| same request, old record `finished` | the original fixed record | no rerun; no new run |
| same request, owner lock valid and executing | `AttemptInProgress(attempt_id, latest_record_ref if any)` | short-circuit before any new binding/start; no wait |
| same request, non-terminal registration but owner lock free/state unknown, or `recovery_required` | `RecoveryRequired(attempt_id, latest_record_ref if any)` | no takeover; no rerun |
| registered budget/resource/capability refusal | must submit `not_started` with reason; on success return the fixed RecordRef | no start; on failed submission no valid new Ref |
| actual candidate identity check fails | after binding actual bytes, before start, as above; carries the failure fact | declared value is not treated as a captured value |
| execution ended but external resource/cleanup unconfirmed | fixed record with `phase=recovery_required` / truthful cleanup | not a re-entry anomaly; not disguised as `finished` |
| submission fails after start | `ExecutionPersistenceError(started=true, partial_record, retained_refs)`; best-effort cleanup | no valid new Ref; an old Ref proves only its own revision |
| top-level prepared/record/attempt/operation/scope mismatch | refuse the handoff; no EvaluationResult/AssessmentRef for this call | diagnosis allowed; must not impersonate a judgment |
| single item inside a correct combination corrupt/foreign/missing | mark invalid/missing; B judges by the frozen claim rule | does not turn the whole correct record into "wrong record" |
| B has a result but assessment archiving fails | result and save failure reported separately; no new AssessmentRef | original RecordRef retained for explicit re-assessment |

For a registered admission or actual-identity refusal that never acquired a cleanable resource, the record encoding is fixed: `phase=finished`, `process_outcome=not_started`, `started=false`, `cleanup=not_needed`. If a lease was acquired, cleanup must be truthful: `confirmed` once confirmed; otherwise `phase=recovery_required`, not a falsified `not_needed`.

Phases: `registered → preparing → running → stopping/finalizing → finished` or `recovery_required`. `registered` does not mean budget was admitted. `process_outcome` is independent: `not_started`/`exited`/`timed_out`/`cancelled`/`execution_error`. Cleanup is independent: `not_needed`/`confirmed`/`failed`/`unknown`; `finished` is not allowed while an external process or lease is unconfirmed. Checks not performed are explicitly `not_performed`; no false positive facts are backfilled.

Recovery only verifies state, supplements submission, or confirms termination/cleanup under existing authorization; it never executes the candidate. Explicit rerun creates a new attempt with retry lineage, still under the original budget scope and deadline. The first phase introduces no cross-process cancel, daemon, or automatic takeover.

Acceptance: T01–T14, T22–T24, including the same-key conflict, finished replay, and candidate-byte mismatch rows.

## D007 — target resource tree

Decision: `knowledge/data/{repo_packs/<pack>/knowledge,skill_library,knowledge_packs,research_profiles}`; `pack.json` separately at `harness/data/repo_packs/<pack>/pack.json`; the dashboard template at `Apps/views/templates/dashboard.html`. K01 migrates seven code modules only and moves no resources; K02 performs the three precise migration classes (importlib, checkout `Path`, plugin physical layout) with old logical pins/hashes preserved and without assuming a single loader.

## D008 — function ownership

Decision: see [`function-owners.md`](function-owners.md). Target new core layout:

- K keeps the seven original file names.
- H: `api.py`, `execution_service.py`, `input_binding.py`, `run_store.py`, `budget.py`, `resources.py`, `records/*`.
- B: `api.py`, `evaluation.py`, `legacy_run_v0.py`, `sampling.py`, `tasks/*`, `workers/*`, `validators/*`.
- Apps: `bridge.py`, `archive.py`, `result.py`, and `views/legacy`.

These are target names; `src` does not yet contain a production RunStore or worker. A name is not evidence that code exists. Mixed responsibilities are split by accepted responsibility, and unlisted unknown functions are not moved by filename.

## D009 — first-wave gate policy

Decision: see [`gate-policy-runstore-and-first-prs.md`](gate-policy-runstore-and-first-prs.md) §B. Seven precise Knowledge root compatibility re-exports are the entire first wave; the current gate is unchanged until a formal architecture review accepts the exact policy first. No dynamic import escapes, no star imports, no `sys.modules` substitution, no blanket `harness/*` exception. `overall isolation_complete` stays false while any allowlist entry or registered residual legacy edge exists.

## D010 — three budget facts and no scope reset

Decision: the ExecutionRecord minimal list, execute delivery, Apps→B mapping, and every figure carry capacity/reservation, actual usage, and releasable leases with confirmed release as three independent facts. Unknown is `null` with a reason; not-applicable is explicit; zero is never used to impersonate unknown; reservations are not charged as actual usage; units are never summed across currencies.

`BudgetAdmission`/`Reservation` are produced by actual H admission, not by an Apps preview. A paid capability with no policy is refused. Free CPU may use `policy_ref=null` only when H confirms there is no paid capability and the task requires no budget policy; scope, deadline, and observable usage are still kept.

Generation, evaluation, and retry share the budget scope and total deadline; they do not reset per attempt, and Apps invents no refund rule. Lease cleanup and cost settlement are recorded separately; an unconfirmed release is not reported as released.

Acceptance: the three facts are independently produced, projected, and read back consistently; a cancellation request is not an exited process or a release; elapsed/call-count/amount units stay independent; `not_started` still records admission and reservation facts that occurred.

## D011 — Knowledge Collection layering and the five-dimension state

Decision: keep `Collection(items, omissions, collisions, no_match, limitations, selection?)` as the public v1; do not add mandatory inspected/eligible/selected/delivered DTO layers. If `selection` is used it must retain the selection rule/version, input candidates/pins, and selection/omission notes. H separately retains the `DeliveredContext` bytes actually delivered; retrieved or selected is not the same as delivered or adopted. The five Bundle dimensions remain separate and are not collapsed into one lifecycle enum.

`create`/`update`/`history` keep existing CAS semantics; an `expected_revision` conflict must fail and is never implicitly overwritten. Storage schema 2 and `_provenance` are internal only; the port returns parsed schema-1 logical records. Author declaration goes through PR/CI and is not pushed into read/collect/update.

Acceptance: a pure-id Bundle read and an authority-qualified read ref are listed separately and history hashes do not change; an unavailable fixed revision fails explicitly; the delivered byte cap applies and omissions are explainable; no-match, collisions, and limitations are never silently dropped.

## D012 — RunStore

Decision: see [`gate-policy-runstore-and-first-prs.md`](gate-policy-runstore-and-first-prs.md) §C. There is no production RunStore in `src`; the design selects a new H-owned local SQLite v1. It does not wrap a nonexistent system and does not treat an in-memory dict as a cross-process implementation. First phase is local filesystem only: no network disk, distributed transaction, daemon, or automatic takeover.

## D013 — legacy entry-point compatibility

Decision: old entry points, arguments, `Path`, stdout, stderr, and old run 0/1/2 stay byte-for-byte compatible. New Apps naming/envelope/exit mapping is fixed by D022, but K01/H01 do not implement a CLI.

## D014 — events remain local, not a new shared event schema

Decision: #333 and #371 are both **Proposed**, not accepted event schemas. `ExecutionEventV1` is only an H-local fact read model; it is explicitly not a new format for #333 production events and does not cross-write legacy `events.jsonl`. Legacy STAGES/verdict capture/reconciliation belong to H; presentation belongs to Apps. A formal unified event implementation belongs to its own later slice and does not block K01/H01.

## D015 / D016 — acceptance numbering and rollout discipline

Decision: D019 fixes the first-wave PRs and their acceptance IDs (A00/A01/K01/K02/H01/H02/B01 rows) and the rollback rules; see [`gate-policy-runstore-and-first-prs.md`](gate-policy-runstore-and-first-prs.md) §D. T01–T40 in [`acceptance-criteria.md`](acceptance-criteria.md) are the single numbered acceptance set; T36–T40 come from D023. Acceptance IDs are expectations, not executed tests.

## D017 — schema, canonical encoding, semantics

Decision: see [`schema-and-semantics.md`](schema-and-semantics.md). Draft 2020-12 library `urn:inferrsi:design:contract-v1`, 106 named definitions, all new fixed-object fields required, unknown fields rejected, null only where the schema permits it. The library root must not be used as a validation target; validate against a named `$defs` entry.

## D018 — first-wave gate policy detail

Decision: [`gate-policy-runstore-and-first-prs.md`](gate-policy-runstore-and-first-prs.md) §B: exact seven leaf→module allowances, shape checks, transitive-closure checks, the registered residual `harness/skill_evaluation.py → knowledge_operations → knowledge.*` edge, the staged isolation test, and the rule that a non-empty allowlist keeps `overall isolation_complete=false`. The skill-evaluation split (#410) later removed the registered residual edge and its exemption.

## D019 — first PRs and numbering

Decision: [`gate-policy-runstore-and-first-prs.md`](gate-policy-runstore-and-first-prs.md) §D. PR-00a (this contract set), PR-00b (gate policy), K01 (seven K modules), H01 (persistent record core), H02 (single-CPU execute), K02 (physical resource migration), then B01/Apps. H01 does not depend on K01 or H02; B/Apps do not block K01/H01.

## Boundaries and ports

| Owner | Port | Input → output | Prohibited side effects / invariant |
| --- | --- | --- | --- |
| K | `read(reference, revision=None)` | `KnowledgeReadRequest` → `KnowledgeReadResult` | returns actual pins; legacy logical record unchanged; read does not write the store or execute |
| K | `collect(query/ids/pins/filters/selection)` | `KnowledgeCollectRequest` → `KnowledgeCollection` | no-match/omission/collision explainable; dynamic selection does not claim permanent replay |
| K | `resource(reference, path, revision)` | `KnowledgeResourceRequest` → result + read-only bytes | revision required; never re-resolves current; path authorization and hash check |
| K | `create`/`update`/`history` | `Knowledge*Request` → fixed revision/history | CAS conflict is not success; no new write-idempotency log |
| B | `prepare(PrepareRequest)` | `PreparedEvaluation`, **plus, outside the object, in-process: canonical bytes of the frozen protocol and a read-only per-role entry to the resource closure** | no execution/budget admission/implicit archiving; freezes legal rules and closure; does not depend on Apps storage routing and does not store bytes itself |
| B | `encode`/`decode`/`validate_prepared` | `PreparedEvaluation` ↔ canonical bytes (may accept an already-assigned location) | pure computation; unknown version is not guessed |
| B | `assess(prepared, evidence, *, artifacts)` | `EvidenceInput` → `EvaluationResult` | reads retained bytes only; no worker/candidate start, no network, no device, no re-measurement |
| B | `claim_keys(protocol)` / `validate_request_key` | frozen protocol → read-only claim-key set, or a business-free projection test of the requested key | read-only; no business judgment; writes no assessment; used by Apps before invoking the assessor (V34) |
| B | `encode`/`decode`/`validate_assessment` | `EvaluationResult` ↔ canonical bytes | only B decides claim/metric/eligibility/comparison |
| H | `allocate_attempt(run_id, owner, target_request, deadline, resources, budget, retry*)` | `AttemptContext` | allocates identity; no budget/resource taken, no start; `request_id=attempt_id` |
| H | `execute(spec, *, cancellation=None)` | `ExecutionSpec` → flat `ExecutionRecord` or a stable `PublicError` class | atomic registration, H admission/binding/run/record; verifies the actual entry code digest before start (V32); not B scoring |
| H | `read(attempt_id, record_version=None)` | flat `ExecutionRecord` | latest means latest committed; a specified version is fixed; no committed record → `NoCommittedRecord`; no snapshot fabrication, no takeover/restart |
| H injected | `CandidateProvider.plan_generation` / `collect_candidate` | `CandidateContext`+`AttemptContext` → `GenerationPlan`; `Record` → `CandidateProposal` | neither end performs external execution; H executes in the middle |
| H injected | `EvaluationPort.execution_for` / `assess` | `PreparedRef`+`CandidateProposal`+`AttemptContext`+scope → `ExecutionSpec`; `Record` → `AssessmentSummary` | implemented by Apps as an adapter; identity/deadline/budget not rebuilt; AssessmentRef only after the result is saved |
| H injected | `ContextProvider.collect` | `ContextRequest` → **`DeliveredContextDraft` (in-process value, not a JSON DTO)** | draft carries no uncommitted valid `ArtifactRef`; H seals `DeliveredContext` through its own capture/commit (V33) |
| Apps | `archive_prepared` / `archive_assessment` / `bind_execution` / `to_evidence` / `to_summary` | fixed bytes → the corresponding Ref; explicit field conversion | Ref only after successful archive; never writes the H RunStore; invents no budget/judgment/scope semantics |
| each module's own reader port | `ArtifactReader.open(ArtifactRef)` | read-only byte stream | authorized root, size/hash, safe path; no network/current fallback and no re-execute |

The D023-1 in-process closure delivery is not a new DTO and does not add a `PreparedEvaluation` field; the final location lives only in the existing `ArtifactRef.location`. The D023-3 `DeliveredContextDraft` is likewise an in-process value type (items/selection/omissions/limitations, bytes or stream, intended logical location and digest); it deliberately is not a `$defs` entry and no serializable stream shape is invented for it.

JSON Schema reference reuse is document structure only. It does not authorize K/H/B importing each other's DTO implementations or a shared business package.

## Canonical encoding summary

`inferrsi-json-v1`: ASCII keys in lexicographic order; UTF-8 with no BOM, no trailing newline, and no insignificant spaces; arrays not reordered; Chinese not escaped and Unicode not normalized; duplicate keys, lone surrogates, float tokens, NaN/Infinity, and integers outside `[-9007199254740991, 9007199254740991]` rejected. Strings use language-independent escaping: `\"`, `\\`, `\b\t\n\f\r`, and lowercase four-digit `\u00xx` for the remaining U+0000–U+001F; every other legal scalar is raw UTF-8, including `/`, U+007F, U+2028/U+2029, and non-BMP characters. Full rules and the identity projection P are in [`schema-and-semantics.md`](schema-and-semantics.md) §3.

## Evidence status and stop boundary

Machine schema fields, canonical rules, and semantic expectations are written down. Actual implementation tests remain implementation acceptance; nothing here claims they passed. The source audit covered known D1–D8 patch seams, private names, lazy imports, and the catalog split; the coverage boundary is disclosed, and no claim is made that all 993 functions were reviewed. H02 helper migration and the full K02 artifact build chain are measured point by point in their own implementation PRs; they are not prerequisites for starting K01/H01.

Final convergence requires a Fable review of this exact version stating no known design blocker, with the author independently reviewing the argument and integrating later source evidence. Repository review acceptance, real tests, and implementation authorization remain separate. All material in this set remains **Proposed**.
