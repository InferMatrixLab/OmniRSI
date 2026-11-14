# Qualify preflight environment (why two Linux/bwrap hosts can disagree)

`inferrsi.harness.artifact_sandbox.replay()` runs a **probe** invocation inside
`bwrap --unshare-all …` with explicit `--ro-bind` of system Python, its stdlib,
lib dirs, a temp `/work`, `/proc` and `/dev`, before any control. If the probe
does not return `{"ready": True}`, it raises `SandboxUnavailable("Artifact
sandbox preflight failed; no host fallback")` and does **not** fall back to the
host. Qualification then reports INCONCLUSIVE with empty controls. That is the
designed failure mode, not a PASS/FAIL on the parser controls.

## PASS-host `preflight-diag` output (attached)

The PASS receipt in `receipts/qualify-8b4a0cf-debian13/` was produced on this
host:

- Linux 6.12.113+ x86_64, Debian GNU/Linux 13.7 (trixie), glibc 2.41
- `/usr/bin/python3` → `/usr/bin/python3.13` = CPython 3.13.5
- bubblewrap 0.12.0 (`/usr/bin/bwrap`)
- Runner-image *label* `debian-13.7/local-box-bwrap-0.12.0` (this is a label,
  not the GitHub `ubuntu-22.04` workflow image)
- **No** `sysctl`, AppArmor policy, or service change was made for the qualify
  run or for the diagnostic below

`environment.json` is the original qualify-run snapshot
(**2026-11-14 17:16:35 Asia/Shanghai**). It records the free-text note
"User namespaces already enabled" but does not include the sysctl / `bwrap`
probe facts that `reproduce.py preflight-diag` prints. Those facts from the
**same PASS host** are in
[`receipts/qualify-8b4a0cf-debian13/preflight-diag-pass-host.txt`](receipts/qualify-8b4a0cf-debian13/preflight-diag-pass-host.txt)
(**2026-11-14 20:59:47 Asia/Shanghai**). Verbatim highlights:

| Fact | PASS host |
| --- | --- |
| `/proc/sys/kernel/unprivileged_userns_clone` | file not present (Linux 6.12) |
| `/proc/sys/user/max_user_namespaces` | `63991` |
| `/proc/sys/kernel/apparmor_restrict_unprivileged_userns` | file not present |
| `/sys/module/apparmor/parameters/enabled` | file not present |
| naive `bwrap --unshare-all -- /bin/true` | exit **1**, `execvp /bin/true: No such file or directory` |
| naive `bwrap --unshare-all -- /usr/bin/true` | exit **1**, same `execvp` ENOENT (`/usr/bin/true` exists on the host) |
| `bwrap --unshare-all --ro-bind / / -- /usr/bin/true` | exit **0** |
| official `sandbox_command(..., probe=True)` | `{"ready": True}` |

The naive `/bin/true` command is **not** the official qualify preflight.
On this bubblewrap 0.12.0, the sandbox root is empty until paths are bound,
so the naive command fails with `execvp` ENOENT **on the same host that
already produced a qualify PASS**. Do not treat a naive-probe failure as
proof that this host cannot qualify, and do not treat a naive-probe success
on another host as a complete diagnosis.

## Failing-host `preflight-diag` output (attached)

The reviewer-side data request is now answered. zengchuang-hw reran the
helper from `7536ddc` against a clean git archive of `03a6a62` on the
Python 3.12.14 review host, **2026-11-14 21:18:11 Asia/Shanghai**
(comment [6877867250](https://github.com/InferMatrixLab/InferRSI/pull/437#issuecomment-6877867250)).
The reviewer already redacted private interpreter/workspace paths. Verbatim
output is in
[`receipts/preflight-diag-failing-host-python3.12.14.txt`](receipts/preflight-diag-failing-host-python3.12.14.txt).

Side-by-side facts (same helper, same `03a6a62` / `8b4a0cf` code tree):

| Fact | PASS host (Debian 13.7 / Python 3.13.5) | Failing review host (launch Python 3.12.14) |
| --- | --- | --- |
| system `/usr/bin/python3` | → `python3.13`, Python 3.13.5 | → `/usr/bin/python3.13`, Python 3.13.5 |
| bubblewrap | 0.12.0 | 0.12.0 |
| `unprivileged_userns_clone` | file not present | file not present |
| `max_user_namespaces` | `63991` | `2147483647` |
| AppArmor userns sysctl / module | file not present | file not present |
| naive `bwrap --unshare-all -- /bin/true` | exit 1, `execvp` ENOENT | exit 1, `loopback: Failed to create NETLINK_ROUTE socket: Operation not permitted` |
| `bwrap --unshare-all --ro-bind / / -- /usr/bin/true` | exit **0** | exit **1**, same `NETLINK_ROUTE` / `Operation not permitted` |
| official `sandbox_command(..., probe=True)` | `{"ready": True}` | `None` / `ready: False` |
| skill_eval_qualify | PASS (attached receipt) | remains INCONCLUSIVE, empty controls (reviewer) |

This is **not** an ENOENT-only difference. On the failing host, even the
root-bound unshare probe is denied `NETLINK_ROUTE` (`Operation not permitted`),
and the official-shaped probe is not ready. Both hosts have system Python
3.13.5, bwrap 0.12.0, no `unprivileged_userns_clone` file, non-zero
`max_user_namespaces`, and no AppArmor userns sysctl/module file. The
observed difference is network-namespace / `NETLINK_ROUTE` permission during
`bwrap --unshare-all`, not a tree disagreement and not a missing `/bin/true`.

The failing-host output does not name the exact enclosing container or
security policy that denies that socket, and no sysctl, AppArmor, network,
or sandbox setting was changed to try to clear it. This pack does **not**
guess a further host policy, does **not** weaken the sandbox or enable host
fallback, and does **not** claim a qualify PASS on the failing host.

This is exploratory host-limitation evidence. It is **not** official
historical replay, not a main-only `skill_eval_cloud_replay` run, and not
official acceptance of Issue 410.

Diagnostic (no sysctl change required to *run the diagnosis*):

```bash
python3 docs/reviews/pr437-s01-reproduction/reproduce.py preflight-diag \
  --repo .
# or, from a git archive of 8b4a0cf / 03a6a62:
# python3 docs/reviews/pr437-s01-reproduction/reproduce.py preflight-diag --repo "$ARCHIVE"
```

Later pack-only commits (including `1132040`, `7536ddc`, and any follow-up
that only edits this directory) are **not** tree-identical to `8b4a0cf`.
