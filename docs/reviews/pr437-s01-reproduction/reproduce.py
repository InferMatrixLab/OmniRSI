#!/usr/bin/env python3
"""Portable reproduction driver for InferRSI PR 437 (S01).

Run from a clone that can resolve the pinned SHAs. Paths are relative to
--scratch or the current working directory. This script does not depend on
any private C:\\ or /workspace host path.

Examples (from the repository root, after: git fetch origin
a590ccb25596396a592f237105166ade4aedeaaa
8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3
03a6a62fab702bee2f730667aa3c6d497079cae4
1052925ff22f9a850cebc64afd0083b35ed1fae9
ca684ed407e19aec60c271f0032790ab0973bff1):

  python3 docs/reviews/pr437-s01-reproduction/reproduce.py preflight-diag
  python3 docs/reviews/pr437-s01-reproduction/reproduce.py trial-bytes --scratch /tmp/s01-repro
  python3 docs/reviews/pr437-s01-reproduction/reproduce.py qualify --revision 8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3 --scratch /tmp/s01-repro
  python3 docs/reviews/pr437-s01-reproduction/reproduce.py replay-a --scratch /tmp/s01-repro
  python3 docs/reviews/pr437-s01-reproduction/reproduce.py replay-b --revision 8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3 --scratch /tmp/s01-repro
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_BEFORE = "a590ccb25596396a592f237105166ade4aedeaaa"
DEFAULT_AFTER = "8b4a0cfbde8a1f726c2ec1dcc4886cbb5f773db3"
TREE_IDENTICAL_HEAD = "03a6a62fab702bee2f730667aa3c6d497079cae4"
HISTORICAL = "1052925ff22f9a850cebc64afd0083b35ed1fae9"
EVIDENCE = "ca684ed407e19aec60c271f0032790ab0973bff1"
OFFICIAL_BUNDLE_SHA256 = "a401b170080e87ce5af14d1f3a49a5495d76d4db8da1353882841c5733b26547"
EXPECTED_FILE_DIGEST = "7457bb7d4b998be31f64d4e9cb25e4e21e0b8bcf21ab8bdd1669496f9f616f61"
EXPECTED_RESULT_DIGEST = "9302f92978c0d97f56e6ebfff463a7aa0ef062c7788b88e75a6d4277c042f782"
EXPECTED_TRIALS = 58
EXPECTED_FILES = 448
# 448 is the Debian 13.7 / Python 3.13.5 Linux reference capture.
# 447 has been observed on Windows and on a Linux Python 3.12.14 review host
# with same-run 0 diffs. That count is a documented capture-set difference,
# not a before/after mismatch.
DOCUMENTED_FILE_COUNTS = frozenset({447, 448})
BUNDLE_URL = (
    "https://raw.githubusercontent.com/InferMatrixLab/InferRSI/"
    f"{EVIDENCE}/artifacts/skill-eval-cloud/bundle.json"
)
RUNNER_IMAGE = "debian-13.7/local-box-bwrap-0.12.0"

BEFORE_MODULES = {
    "trial-entry": "inferrsi.harness.skill_evaluation",
    "executor": "inferrsi.harness.skill_evaluation",
    "engineering-task": "inferrsi.harness.skill_eval_engineering",
    "engineering-verify": "inferrsi.harness.skill_eval_engineering",
    "sandbox": "inferrsi.harness.skill_eval_engineering",
    "diagnostic": "inferrsi.harness.skill_eval_diagnostic",
    "improvement": "inferrsi.harness.improvement",
}
AFTER_MODULES = {
    "trial-entry": "inferrsi.apps.skill_evaluation",
    "executor": "inferrsi.harness.skill_evaluation",
    "engineering-task": "inferrsi.benchmark.skill_eval_engineering",
    "engineering-verify": "inferrsi.apps.skill_eval_engineering",
    "sandbox": "inferrsi.harness.artifact_sandbox",
    "diagnostic": "inferrsi.benchmark.skill_eval_diagnostic",
    "improvement": "inferrsi.apps.improvement",
}


def repo_from(args) -> Path:
    if args.repo:
        return Path(args.repo).resolve()
    # docs/reviews/pr437-s01-reproduction -> repository root
    guessed = HERE.parents[2]
    if (guessed / ".git").exists() or (guessed / "tools" / "skill_eval_qualify.py").exists():
        return guessed
    return Path.cwd().resolve()


def run(cmd, *, cwd=None, env=None, check=True):
    print("+", " ".join(str(part) for part in cmd), flush=True)
    completed = subprocess.run(cmd, cwd=cwd, env=env)
    if check and completed.returncode != 0:
        raise SystemExit(completed.returncode)
    return completed.returncode


def git_archive(repo: Path, sha: str, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    proc = subprocess.run(
        ["git", "archive", sha], cwd=repo, stdout=subprocess.PIPE, check=True
    )
    subprocess.run(["tar", "-x", "-C", str(dest)], input=proc.stdout, check=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(files: dict) -> str:
    lines = "".join(f"{digest}  {name}\n" for name, digest in sorted(files.items()))
    return hashlib.sha256(lines.encode()).hexdigest()


def _print_sys_file(path: Path) -> None:
    if path.exists():
        try:
            value = path.read_text().strip().replace("\n", " ")
        except OSError as error:
            print(f"{path}: (unreadable: {error})")
            return
        if len(value) > 200:
            value = value[:200] + "…"
        print(f"{path}: {value}")
    else:
        print(f"{path}: (file not present)")


def _run_probe(label: str, cmd: list[str]) -> int:
    print(f"probe[{label}]:", " ".join(cmd))
    completed = subprocess.run(cmd, capture_output=True, text=True)
    print(f"probe[{label}] exit:", completed.returncode)
    err = (completed.stderr or completed.stdout or "").strip()
    if err:
        print(err[:1000])
    return completed.returncode


def preflight_diag(args) -> int:
    """Print host facts. Does not change sysctl, AppArmor, or the sandbox."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    print("captured_at_asia_shanghai:", now.strftime("%Y-%m-%d %H:%M:%S %Z (UTC+8)"))
    print("platform:", sys.platform)
    print("python:", sys.version.replace("\n", " "))
    print("executable:", sys.executable)
    system_py = Path("/usr/bin/python3")
    resolved = str(system_py.resolve()) if system_py.exists() else "n/a"
    print("system_/usr/bin/python3:", system_py, "exists=" + str(system_py.exists()),
          "resolved=" + resolved)
    if system_py.exists():
        ver = subprocess.run([str(system_py), "--version"], capture_output=True, text=True)
        print("system_python_version:", (ver.stdout or ver.stderr or "").strip())
    bwrap = shutil.which("bwrap")
    print("bwrap:", bwrap)
    if bwrap:
        ver = subprocess.run([bwrap, "--version"], capture_output=True, text=True)
        print("bwrap_version:", (ver.stdout or ver.stderr or "").strip())

    _print_sys_file(Path("/proc/sys/kernel/unprivileged_userns_clone"))
    _print_sys_file(Path("/proc/sys/user/max_user_namespaces"))
    _print_sys_file(Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns"))
    _print_sys_file(Path("/sys/module/apparmor/parameters/enabled"))

    print("note: the next two probes are NOT the official qualify preflight.")
    print("bubblewrap's sandbox root is empty until paths are bound, so")
    print("`bwrap --unshare-all -- /bin/true` can exit 1 with execvp ENOENT")
    print("on a host where official qualify still PASSes.")
    if bwrap:
        _run_probe("naive-/bin/true", [bwrap, "--unshare-all", "--", "/bin/true"])
        _run_probe("naive-/usr/bin/true", [bwrap, "--unshare-all", "--", "/usr/bin/true"])
        true = "/usr/bin/true" if Path("/usr/bin/true").exists() else "/bin/true"
        _run_probe("unshare-all-ro-bind-root",
                   [bwrap, "--unshare-all", "--ro-bind", "/", "/", "--", true])

    # Official qualify-shaped probe, when this tree's sandbox module is importable.
    repo = None
    if getattr(args, "repo", None):
        repo = Path(args.repo).resolve()
    else:
        guessed = HERE.parents[2]
        if (guessed / "src" / "inferrsi" / "harness" / "artifact_sandbox.py").is_file():
            repo = guessed
    if repo is None:
        print("qualify_shaped_probe: skipped (no --repo and this file is not inside a clone)")
        print("Re-run with --repo pointing at a git archive or clone of 8b4a0cf / 03a6a62.")
    else:
        src = repo / "src"
        sys.path.insert(0, str(src))
        try:
            from inferrsi.harness.artifact_sandbox import (  # type: ignore
                RUNNER, SandboxUnavailable, _invoke,
            )
        except Exception as error:
            print("qualify_shaped_probe: import failed:", type(error).__name__, error)
        else:
            import tempfile
            with tempfile.TemporaryDirectory(prefix="s01-preflight-probe-") as temporary:
                work = Path(temporary)
                (work / "runner.py").write_text(RUNNER, encoding="utf-8")
                (work / "parser.py").write_text(
                    "def evaluate(*_a):\n    return True\n", encoding="utf-8",
                )
                try:
                    result = _invoke(work, probe=True)
                except SandboxUnavailable as error:
                    print("qualify_shaped_probe: SandboxUnavailable:", error)
                    result = None
            print("qualify_shaped_probe result:", result)
            print("qualify_shaped_probe ready:", result == {"ready": True})
            print("qualify_shaped_probe: official sandbox_command binds system")
            print("Python, stdlib, lib dirs, a temp /work, /proc and /dev; it is")
            print("not `bwrap --unshare-all -- /bin/true`.")

    print("This host is a local Linux qualify host only if the qualify-shaped")
    print("probe is ready and /usr/bin/python3 is a system CPython 3.x. It is")
    print("not the GitHub ubuntu-22.04 official workflow runner.")
    return 0


def capture_one(repo_tree: Path, work: Path, output: Path, modules: dict) -> None:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_tree / "src")
    env["PYTHONUTF8"] = "1"
    cmd = [
        sys.executable, str(HERE / "trial_bytes.py"), "run",
        "--repo", str(repo_tree),
        "--work", str(work),
        "--output", str(output),
    ]
    for flag, value in modules.items():
        cmd.extend(["--" + flag, value])
    run(cmd, env=env)


def accept_trial_bytes(
    *,
    compare_rc: int,
    trial_count: int,
    file_count: int,
    digest_files: str,
    digest_results: str,
    require_historical: bool = False,
) -> tuple[int, list[str]]:
    """Separate same-run parity (portable exit) from historical-reference digests.

    Path-bearing trial ``result.json`` / ``execution.json`` records embed the
    caller scratch/work directory. A fresh scratch can therefore have 0
    before/after diffs and still disagree with the pinned Linux receipt
    digests. Those pins remain a reference; they must not fail portable
    reviewer re-runs.
    """
    lines: list[str] = []
    parity_ok = (
        compare_rc == 0
        and trial_count == EXPECTED_TRIALS
        and file_count in DOCUMENTED_FILE_COUNTS
    )
    historical_ok = (
        file_count == EXPECTED_FILES
        and digest_files == EXPECTED_FILE_DIGEST
        and digest_results == EXPECTED_RESULT_DIGEST
    )
    lines.append(
        "same-run parity: {0} (compare_exit={1}, trials={2}, files={3})".format(
            "PASS" if parity_ok else "FAIL", compare_rc, trial_count, file_count,
        )
    )
    if (
        compare_rc == 0
        and trial_count == EXPECTED_TRIALS
        and file_count != EXPECTED_FILES
        and file_count in DOCUMENTED_FILE_COUNTS
    ):
        lines.append(
            "NOTE: retained file count is {0}, not the Linux reference 448. "
            "This is a documented capture-set difference (observed on Windows "
            "and on a Linux Python 3.12.14 review host). Do not compare the "
            "file-tree digest to the Linux pin.".format(file_count)
        )
    if historical_ok:
        lines.append(
            "historical-reference digest: MATCH (Linux 448-file pin; host "
            "paths coincide with the original receipt)"
        )
    else:
        lines.append(
            "historical-reference digest: not matched (informational). "
            "Path-bearing result.json embeds the scratch/work directory, so "
            "a fresh scratch is not expected to match the pinned Linux "
            "digests {0}… / {1}…".format(
                EXPECTED_FILE_DIGEST[:8], EXPECTED_RESULT_DIGEST[:8],
            )
        )
        lines.append("  expected files digest: " + EXPECTED_FILE_DIGEST)
        lines.append("  actual   files digest: " + digest_files)
        lines.append("  expected result.json digest: " + EXPECTED_RESULT_DIGEST)
        lines.append("  actual   result.json digest: " + digest_results)
        if require_historical:
            lines.append(
                "historical-reference digest required by "
                "--require-historical-digest; exiting 2"
            )
    if not parity_ok:
        return 2, lines
    if require_historical and not historical_ok:
        return 2, lines
    return 0, lines


def trial_bytes(args) -> int:
    repo = repo_from(args)
    scratch = Path(args.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    before_tree = scratch / "tree-before"
    after_tree = scratch / "tree-after"
    shared_work = scratch / "shared-work"
    before_out = scratch / "bytes-before"
    after_out = scratch / "bytes-after"
    if shared_work.exists():
        shutil.rmtree(shared_work)
    shared_work.mkdir(parents=True)
    git_archive(repo, args.before, before_tree)
    git_archive(repo, args.after, after_tree)
    capture_one(before_tree, shared_work, before_out, BEFORE_MODULES)
    capture_one(after_tree, shared_work, after_out, AFTER_MODULES)
    compare = subprocess.run(
        [sys.executable, str(HERE / "trial_bytes.py"), "compare",
         str(before_out), str(after_out)]
    )
    table = subprocess.run(
        [sys.executable, str(HERE / "hash_table.py"), str(before_out), str(after_out)],
        check=True, capture_output=True, text=True,
    )
    sys.stdout.write(table.stdout)
    after_manifest = json.loads((after_out / "manifest.json").read_text(encoding="utf-8"))
    files = after_manifest["files"]
    results = {name: digest for name, digest in files.items()
               if name.endswith("/result.json") and "/verification/" not in name
               and not name.startswith("pure/")}
    digest_files = tree_digest(files)
    digest_results = tree_digest(results)
    exit_code, lines = accept_trial_bytes(
        compare_rc=compare.returncode,
        trial_count=len(results),
        file_count=len(files),
        digest_files=digest_files,
        digest_results=digest_results,
        require_historical=bool(getattr(args, "require_historical_digest", False)),
    )
    for line in lines:
        print(line)
    return exit_code


def qualify(args) -> int:
    repo = repo_from(args)
    scratch = Path(args.scratch).resolve()
    tree = scratch / f"tree-{args.revision[:12]}"
    git_archive(repo, args.revision, tree)
    output = scratch / f"qualify-{args.revision[:12]}"
    if output.exists():
        shutil.rmtree(output)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tree / "src")
    env["PYTHONUTF8"] = "1"
    rc = run([
        sys.executable, "-m", "tools.skill_eval_qualify",
        "--output", str(output),
        "--revision", args.revision,
        "--runner-image", args.runner_image,
    ], cwd=tree, env=env, check=False)
    receipt = output / "qualification.json"
    if receipt.is_file():
        print(receipt.read_text(encoding="utf-8"))
    return rc


def fetch_official_bundle(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        from urllib.request import urlopen
        with urlopen(BUNDLE_URL, timeout=60) as response:
            dest.write_bytes(response.read())
    except Exception as exc:  # noqa: BLE001 — surface a clear reviewer action
        print("Failed to download the official bundle from", BUNDLE_URL)
        print("Error:", exc)
        print("A reviewer with git can also run:")
        print(f"  git fetch origin {EVIDENCE} && git show {EVIDENCE}:artifacts/skill-eval-cloud/bundle.json > {dest}")
        raise SystemExit(2) from exc
    digest = sha256_file(dest)
    if digest != OFFICIAL_BUNDLE_SHA256:
        print("bundle sha256 mismatch:", digest, "expected", OFFICIAL_BUNDLE_SHA256)
        raise SystemExit(2)
    print("official bundle sha256 ok:", digest)
    return dest


def replay_a(args) -> int:
    repo = repo_from(args)
    scratch = Path(args.scratch).resolve()
    tree = scratch / "tree-historical-1052925"
    git_archive(repo, HISTORICAL, tree)
    bundle = fetch_official_bundle(scratch / "official-bundle.json")
    output = scratch / "replay-A"
    if output.exists():
        shutil.rmtree(output)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tree / "src")
    env["PYTHONUTF8"] = "1"
    # The CLI fetches from raw GitHub by evidence commit; passing the pin is enough
    # when the network can reach raw.githubusercontent.com. Keep a local copy for
    # the sha256 check above.
    _ = bundle
    return run([
        sys.executable, "-m", "tools.skill_eval_cloud_replay",
        "--evidence-commit", EVIDENCE,
        "--bundle-sha256", OFFICIAL_BUNDLE_SHA256,
        "--revision", HISTORICAL,
        "--runner-image", args.runner_image,
        "--output", str(output),
    ], cwd=tree, env=env, check=False)


def replay_b(args) -> int:
    repo = repo_from(args)
    scratch = Path(args.scratch).resolve()
    tree = scratch / f"tree-{args.revision[:12]}"
    git_archive(repo, args.revision, tree)
    fetch_official_bundle(scratch / "official-bundle.json")
    output = scratch / "replay-B"
    if output.exists():
        shutil.rmtree(output)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tree / "src")
    env["PYTHONUTF8"] = "1"
    return run([
        sys.executable, "-m", "tools.skill_eval_cloud_replay",
        "--evidence-commit", EVIDENCE,
        "--bundle-sha256", OFFICIAL_BUNDLE_SHA256,
        "--revision", args.revision,
        "--runner-image", args.runner_image,
        "--output", str(output),
    ], cwd=tree, env=env, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", help="Git clone that contains the pinned SHAs")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("preflight-diag", help="Print sandbox-preflight environment facts")
    p.add_argument("--repo", help="Clone or git-archive tree that contains src/inferrsi/harness/artifact_sandbox.py")
    p.set_defaults(handler=preflight_diag)

    p = sub.add_parser("trial-bytes", help="Shared-work trial-record capture and compare")
    p.add_argument("--scratch", required=True, help="Writable directory for archives and outputs")
    p.add_argument("--before", default=DEFAULT_BEFORE)
    p.add_argument("--after", default=DEFAULT_AFTER)
    p.add_argument(
        "--require-historical-digest",
        action="store_true",
        help=(
            "Also require the pinned Linux 448-file receipt digests. "
            "A fresh scratch is not expected to match; same-run parity is "
            "the default portable acceptance."
        ),
    )
    p.set_defaults(handler=trial_bytes)

    p = sub.add_parser("qualify", help="Real bwrap skill_eval_qualify (Linux only)")
    p.add_argument("--scratch", required=True)
    p.add_argument("--revision", default=DEFAULT_AFTER)
    p.add_argument("--runner-image", default=RUNNER_IMAGE)
    p.set_defaults(handler=qualify)

    p = sub.add_parser("replay-a", help="Official bundle on historical controller 1052925")
    p.add_argument("--scratch", required=True)
    p.add_argument("--runner-image", default=RUNNER_IMAGE)
    p.set_defaults(handler=replay_a)

    p = sub.add_parser("replay-b", help="Official bundle on the S01 tree (expected reject)")
    p.add_argument("--scratch", required=True)
    p.add_argument("--revision", default=DEFAULT_AFTER)
    p.add_argument("--runner-image", default=RUNNER_IMAGE)
    p.set_defaults(handler=replay_b)

    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
