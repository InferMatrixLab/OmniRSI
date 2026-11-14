# PR 437 S01 — reviewer reproduction pack

This directory is the runnable receipt for the S01 skill-eval split. It is meant
to be run from a public clone of `InferMatrixLab/InferRSI`. It does not depend
on any private `C:\…` or `/workspace/…` path.

Executed tests and sandbox qualification that produced the receipts below were
run on `8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3`.
`03a6a62fab702bee2f730667aa3c6d497079cae4` has the **identical git tree**
`8ec6a25832e9ef5c9a8134503bcf9bf4673d1cdd`, so those results bind to that
tree. They were **not** originally executed as a fresh checkout of `03a6a62`.
If this pack lands in a later commit, that new commit changes the tree (docs
and scripts only, under this directory); do not call the new commit
tree-identical to `8b4a0cf`. Re-run the commands on the new head before
claiming a new-SHA result.

The compare fixture is the in-tree test fixture set under `tests/fixtures/`
inside each archived SHA (no extra fixture tarball). The shared work directory
is created by the driver (`--scratch …/shared-work`) so host paths recorded
inside `result.json` / `execution.json` do not spuriously differ.

## Pins

| Role | SHA / value |
| --- | --- |
| Pre-S01 baseline (trial-bytes “before”) | `a590ccb25596396a592f237105166ade4aedeaaa` |
| Executed S01 head (receipts below) | `8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3` |
| Clean-history head, same tree as `8b4a0cf` | `03a6a62fab702bee2f730667aa3c6d497079cae4` |
| Historical official controller (Run A) | `1052925ff22f9a850cebc64afd0083b35ed1fae9` |
| Official evidence commit | `ca684ed407e19aec60c271f0032790ab0973bff1` |
| Official bundle SHA-256 | `a401b170080e87ce5af14d1f3a49a5495d76d4db8da1353882841c5733b26547` |
| Official bundle (public raw) | https://raw.githubusercontent.com/InferMatrixLab/InferRSI/ca684ed407e19aec60c271f0032790ab0973bff1/artifacts/skill-eval-cloud/bundle.json |
| Pre-split `grader_sha256` | `c83097f6069dd3e09046080b61c88aebc0922d09aab3fd5f1a03d8f04ec976f2` |
| Post-split `verifier_sha256` / grader of `benchmark/skill_eval_engineering.py` | `40cabccb597c711e2b93814d18500c03e97affd498bc5c59fa8a53a01fa7f283` |
| Correct-control fixture SHA-256 | `20d459826f83f2ae8751e39bfde33309c5cff71b67974da2d80379f38c9dd1d7` |
| Generation Actions run / artifact | [41028614568](https://github.com/InferMatrixLab/InferRSI/actions/runs/41028614568) / id `12965020905`, zip SHA-256 `f0cc1da4f2acf41f275362ed1a4ba68084372e1715d4a00a9f4e82db9aec49c0`, listed as unexpired until **2026-11-29 16:47:47 Asia/Shanghai**. This artifact is **not** required for trial-bytes, qualify, Run A, or Run B. Downloading it typically needs authenticated `gh` access to Actions; there is no public raw URL. It is required only if someone wants to re-run official `skill_eval_cloud_export` against the original generation layout. |

Fetch the SHAs once:

```bash
git fetch origin \
  a590ccb25596396a592f237105166ade4aedeaaa \
  8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3 \
  03a6a62fab702bee2f730667aa3c6d497079cae4 \
  1052925ff22f9a850cebc64afd0083b35ed1fae9 \
  ca684ed407e19aec60c271f0032790ab0973bff1
```

## 1. Trial-record byte identity (Linux reference)

Original capture: Debian GNU/Linux 13.7, Python 3.13.5, `git archive` copies, **shared** `--work` directory, **2026-11-14 17:20:57–17:21:12 Asia/Shanghai**, compare exit 0.

Portable same-run acceptance (driver exit 0): **58** trial records, **0**
differences, and a documented retained-file count of **448** or **447**.
`result.json` / `execution.json` embed the caller scratch/work path, so a
*fresh* scratch is **not** expected to match the historical Linux tree
digests below. Those pins describe the original receipt only; they are
printed as an informational historical-reference check. Use
`--require-historical-digest` only if you intend to fail when the pins
do not match.

Original Linux reference capture (not the portable exit condition): Debian
GNU/Linux 13.7, Python 3.13.5, **448** files, tree digest over all files
`7457bb7d4b998be31f64d4e9cb25e4e21e0b8bcf21ab8bdd1669496f9f616f61`,
tree digest over trial `result.json`
`9302f92978c0d97f56e6ebfff463a7aa0ef062c7788b88e75a6d4277c042f782`.
Per-record table: [`receipts/TRIAL-RECORD-HASHES-8b4a0cf-vs-a590ccb.md`](receipts/TRIAL-RECORD-HASHES-8b4a0cf-vs-a590ccb.md).

The same 58/448, 0 differences, holds for `8b4a0cf` vs `9218391c40ead659403ce76906f1d07a02e040dc` (the merge did not change S01 trial bytes).

Re-run (from the repository root; `--after` may be `8b4a0cf…` or `03a6a62…` — same S01 source tree). Use a **new** empty `--scratch` directory; do not reuse a previous capture:

```bash
python3 docs/reviews/pr437-s01-reproduction/reproduce.py trial-bytes \
  --scratch ./tmp/s01-repro \
  --before a590ccb25596396a592f237105166ade4aedeaaa \
  --after 8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3
```

Equivalent manual commands the driver issues (module maps are required because
the split changes import paths):

```bash
# shared work directory is mandatory; different --work paths create host-path
# diffs in result.json / execution.json that are not S01 content changes.
BEFORE=./tmp/s01-repro/tree-before
AFTER=./tmp/s01-repro/tree-after
WORK=./tmp/s01-repro/shared-work
git archive a590ccb25596396a592f237105166ade4aedeaaa | tar -x -C "$BEFORE"
git archive 8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3 | tar -x -C "$AFTER"

PYTHONUTF8=1 PYTHONPATH="$BEFORE/src" python3 docs/reviews/pr437-s01-reproduction/trial_bytes.py run \
  --repo "$BEFORE" --work "$WORK" --output ./tmp/s01-repro/bytes-before \
  --trial-entry inferrsi.harness.skill_evaluation \
  --executor inferrsi.harness.skill_evaluation \
  --engineering-task inferrsi.harness.skill_eval_engineering \
  --engineering-verify inferrsi.harness.skill_eval_engineering \
  --sandbox inferrsi.harness.skill_eval_engineering \
  --diagnostic inferrsi.harness.skill_eval_diagnostic \
  --improvement inferrsi.harness.improvement

PYTHONUTF8=1 PYTHONPATH="$AFTER/src" python3 docs/reviews/pr437-s01-reproduction/trial_bytes.py run \
  --repo "$AFTER" --work "$WORK" --output ./tmp/s01-repro/bytes-after \
  --trial-entry inferrsi.apps.skill_evaluation \
  --executor inferrsi.harness.skill_evaluation \
  --engineering-task inferrsi.benchmark.skill_eval_engineering \
  --engineering-verify inferrsi.apps.skill_eval_engineering \
  --sandbox inferrsi.harness.artifact_sandbox \
  --diagnostic inferrsi.benchmark.skill_eval_diagnostic \
  --improvement inferrsi.apps.improvement

python3 docs/reviews/pr437-s01-reproduction/trial_bytes.py compare \
  ./tmp/s01-repro/bytes-before ./tmp/s01-repro/bytes-after
# expected: files: before=<447 or 448> after=<same> different=0
#           trial result.json records compared: 58
#           compare exit 0; driver same-run parity PASS (exit 0)
#           historical-reference digest may be "not matched" on a fresh scratch

python3 docs/reviews/pr437-s01-reproduction/hash_table.py \
  ./tmp/s01-repro/bytes-before ./tmp/s01-repro/bytes-after
```

Capture-set difference (not a before/after mismatch): the Debian 13.7 /
Python 3.13.5 reference retains **448** files; **447** has been observed on
Windows and on a Linux Python 3.12.14 review host with same-run 0 diffs.
Do not compare a 447-file tree digest to the Linux 448-file pin. The 58
`result.json` records plus compare exit 0 are the portable acceptance rows.

Minimal acceptance unit tests (not collected by repository pytest):

```bash
python3 docs/reviews/pr437-s01-reproduction/test_acceptance.py
```

## 2. Real Linux sandbox qualification

Original receipt (not GitHub `ubuntu-22.04`): Debian GNU/Linux 13.7, Python
3.13.5, bubblewrap 0.12.0, user namespaces already enabled, no sysctl or
security-policy change. Window **2026-11-14 17:16:34–17:16:35 Asia/Shanghai**,
exit 0, verdict **PASS**.

Receipts (verbatim):

- [`receipts/qualify-8b4a0cf-debian13/qualification.json`](receipts/qualify-8b4a0cf-debian13/qualification.json)
- [`receipts/qualify-8b4a0cf-debian13/environment.json`](receipts/qualify-8b4a0cf-debian13/environment.json)
- [`receipts/qualify-8b4a0cf-debian13/preflight-diag-pass-host.txt`](receipts/qualify-8b4a0cf-debian13/preflight-diag-pass-host.txt) — `preflight-diag` stdout from the same PASS host, **2026-11-14 20:59:47 Asia/Shanghai**, including `unprivileged_userns_clone` (file not present), `max_user_namespaces` (`63991`), AppArmor sysctls (absent), naive `bwrap --unshare-all -- /bin/true` (exit 1, `execvp` ENOENT; this is **not** the official probe), `--ro-bind / /` (exit 0), and official `sandbox_command(..., probe=True)` `{"ready": True}`.
- [`receipts/preflight-diag-failing-host-python3.12.14.txt`](receipts/preflight-diag-failing-host-python3.12.14.txt) — reviewer-posted output (comment 6877867250, **2026-11-14 21:18:11 Asia/Shanghai**) from the Python 3.12.14 review host, helper at `7536ddc`, clean archive of `03a6a62`. Official probe `ready: False`; even `--ro-bind / /` fails with `NETLINK_ROUTE` `Operation not permitted`. This is **not** a qualify PASS and not official historical replay. See [PREFLIGHT-ENVIRONMENT.md](PREFLIGHT-ENVIRONMENT.md).

Controls: `correct.passed` true (19/19 assertions true); `bad_starter.passed`
false (only `length` and `http_failure` true, as designed). No host fallback.

Re-run on Linux with working unprivileged user namespaces:

```bash
python3 docs/reviews/pr437-s01-reproduction/reproduce.py preflight-diag --repo .
python3 docs/reviews/pr437-s01-reproduction/reproduce.py qualify \
  --scratch ./tmp/s01-repro \
  --revision 8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3 \
  --runner-image debian-13.7/local-box-bwrap-0.12.0
```

Equivalent official CLI, from a `git archive` of the revision:

```bash
PYTHONUTF8=1 PYTHONPATH=src python3 -m tools.skill_eval_qualify \
  --output ./tmp/s01-repro/qualify \
  --revision 8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3 \
  --runner-image debian-13.7/local-box-bwrap-0.12.0
```

If preflight fails with `Artifact sandbox preflight failed; no host fallback`
and empty controls, that is INCONCLUSIVE, not a contradiction of the Debian
13.7 receipt. The attached failing-host log shows the official probe is not
ready because `bwrap --unshare-all` cannot create a `NETLINK_ROUTE` socket
(`Operation not permitted`), including with `--ro-bind / /`. See
[PREFLIGHT-ENVIRONMENT.md](PREFLIGHT-ENVIRONMENT.md). This qualify is a real
bwrap run on a local Linux host, **not** the official GitHub `ubuntu-22.04`
workflow image.

## 3. Historical / official replay — keep A, B, and C distinct

Download the official published bundle (public, HTTP 200, no auth):

```bash
curl -fsSL -o official-bundle.json \
  https://raw.githubusercontent.com/InferMatrixLab/InferRSI/ca684ed407e19aec60c271f0032790ab0973bff1/artifacts/skill-eval-cloud/bundle.json
sha256sum official-bundle.json
# expect a401b170080e87ce5af14d1f3a49a5495d76d4db8da1353882841c5733b26547
```

Alternatively: `git show ca684ed407e19aec60c271f0032790ab0973bff1:artifacts/skill-eval-cloud/bundle.json`.

The official CLI `tools.skill_eval_cloud_replay` itself fetches that same path
from raw GitHub when given `--evidence-commit` and `--bundle-sha256`.

### Run A — historical controller, official bundle (this is historical-version reproduction)

```bash
python3 docs/reviews/pr437-s01-reproduction/reproduce.py replay-a --scratch ./tmp/s01-repro
```

Equivalent:

```bash
# cwd = git archive of 1052925ff22f9a850cebc64afd0083b35ed1fae9
PYTHONUTF8=1 PYTHONPATH=src python3 -m tools.skill_eval_cloud_replay \
  --evidence-commit ca684ed407e19aec60c271f0032790ab0973bff1 \
  --bundle-sha256 a401b170080e87ce5af14d1f3a49a5495d76d4db8da1353882841c5733b26547 \
  --revision 1052925ff22f9a850cebc64afd0083b35ed1fae9 \
  --runner-image debian-13.7/local-box-bwrap-0.12.0 \
  --output ./tmp/s01-repro/replay-A
```

Original: **2026-11-14 17:17:59–17:18:01 Asia/Shanghai**, exit 0, verdict **PASS**,
both arms 19/19, qualification PASS. Receipt:
[`receipts/replay-A-historical-1052925/replay.json`](receipts/replay-A-historical-1052925/replay.json).

This is **not** a current-head result.

### Run B — S01 tree, official (old) bundle (expected reject)

```bash
python3 docs/reviews/pr437-s01-reproduction/reproduce.py replay-b \
  --scratch ./tmp/s01-repro \
  --revision 8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3
```

Original: **2026-11-14 17:18:01 Asia/Shanghai**, exit 1, verdict **INCONCLUSIVE**,
`error_category` `ValueError`. The post-split controller rejects the published
bundle because `grader_sha256` `c83097f6…` is not the post-split digest
`40cabccb…`. Receipt:
[`receipts/replay-B-8b4a0cf-official-old-bundle/replay.json`](receipts/replay-B-8b4a0cf-official-old-bundle/replay.json).

This is the **safe, correct** closed-world result. It is **not** “历史回放通过”
on the current head, and the checker was not loosened.

### Run C — exploratory new verification revision (not official)

A local retarget of **only** `grader_sha256` to `40cabccb…`, with
`request_sha256` / parser / inputs / protocol hashes unchanged, can be replayed
and PASSes. Official `python3 -m tools.skill_eval_cloud_export` on `8b4a0cf`
against the original generation layout **exits 1**:
`ValueError: Controller changed after freeze: tools/skill_eval_compare.py`
(sanitized log:
[`receipts/export-cli-refused/official-export-cli-8b4a0cf.sanitized.log`](receipts/export-cli-refused/official-export-cli-8b4a0cf.sanitized.log)).
The freeze already differs on more than the engineering file (S01 moves **and**
pre-S01 controller changes already present on `a590ccb`). Run C was **not**
produced by a successful official export. **Do not treat Run C as #410 official
replay passing, and do not treat it as the 2026-10-30 bundle passing as-is.**

## 4. Official main-only workflow was not executed

`.github/workflows/skill-eval-cloud-replay.yml` is **main-only**. It cannot be
dispatched on this PR branch before merge, and this delivery does **not**
dispatch it. Official export of a *new* plan/bundle likewise belongs on
`main` after the code lands, by a qualified operator, after an owner/reviewer
decision on the narrowed grader digest (see below). That remaining item does
not expand policy and does not loosen the checker.

The 14-day Actions artifact for historical *replay* run
[41029566717](https://github.com/InferMatrixLab/InferRSI/actions/runs/41029566717)
has expired (`total_count: 0`). The bundle bytes remain in the evidence
commit, which is sufficient for Run A / Run B.

## 5. What is still an owner / independent-reviewer decision

#410 official acceptance is **not** declared here. Still for the original
owner or an independent reviewer:

1. Whether rejecting pre-split bundles (Run B) plus a *post-merge* official
   export/replay of a new plan is an acceptable fulfilment of #410’s
   “skill-eval workflow 与历史回放通过”, instead of a pre-merge official
   main-only dispatch (which the workflow cannot do).
2. The narrowed `grader_sha256` coverage (Benchmark rules file only; sandbox
   bytes bind through `controllers` / `revision`).
3. Settlement of function-owner / gate-policy §9 interpretations
   (`usage_totals` stays in H; `SelectionPolicy` moves to B as a whole).

Do not close #410 from this pack. This document uses `Refs #410` only.

## 6. Focused tests (optional, Linux)

Original on `8b4a0cf`, Debian 13.7 / Python 3.13.5, single pytest process,
**2026-11-14 17:17:45–17:17:59 Asia/Shanghai**, exit 0: **194 passed, 56
subtests, 0 skipped**. This is a local Linux archive run, not GitHub
ubuntu-22.04. The same seven files on the same host against `9218391` also
produced 194/56/0; an earlier Windows run on `9218391` was 200/58/2 (platform
collection, not a merge regression).
