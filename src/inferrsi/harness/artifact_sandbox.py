# Harness artifact capture and sandboxed replay for one submitted parser file.
# Every artifact invocation is confined by bubblewrap; absence of that boundary is
# an infrastructure error, never permission to execute the artifact on the host.
# This module holds no task vectors, expected results or verdict rules, and it
# does not sandbox the agent that produced the artifact.

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from .local_process import _stop_tree


class SandboxUnavailable(RuntimeError):
    """The required artifact execution boundary could not be established."""


def read_regular(path: Path, limit: int) -> bytes:
    """Bounded no-follow snapshot after the producer has stopped.

    Reject symlink ancestors and special files; this is not an adversarial
    directory-rename sandbox. The actual replay has a separate OS boundary.
    """
    path = path.absolute()
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("Artifact/evidence paths must not contain symlinks")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError("Cannot read regular evidence file") from error
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Evidence is not a bounded regular file")
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Evidence exceeds byte bound")
    return data


def input_hashes(workspace: Path) -> dict:
    """Frozen engineering inputs only; special nodes are never opened."""
    if any(path.is_symlink() for path in (workspace.absolute(), *workspace.absolute().parents)):
        raise ValueError("Workspace paths must not contain symlinks")
    hashes = {}
    for path in workspace.rglob("*"):
        name = path.relative_to(workspace).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError("Workspace contains a symlink or special node")
        if stat.S_ISREG(mode) and name != "parser.py":
            hashes[name] = hashlib.sha256(read_regular(path, 16 * 1024 * 1024)).hexdigest()
    return hashes


def materialize_generated_artifact(workspace: Path, answer: dict, starter: bytes,
                                   limit: int) -> dict:
    """Convert a bounded provider response to one artifact; never execute its source.

    The provider is a structured-output service, not a process with workspace access.
    Fresh workspace ownership and the unchanged ``starter`` are required before writing.
    """
    if (not isinstance(answer, dict)
            or set(answer) != {"artifact", "summary", "artifact_source"}
            or answer["artifact"] != "parser.py"
            or not isinstance(answer["summary"], str)
            or not isinstance(answer["artifact_source"], str)):
        raise ValueError("Invalid generated artifact answer")
    try:
        source = answer["artifact_source"].encode("utf-8")
    except UnicodeError:
        raise ValueError("Generated artifact must be UTF-8") from None
    if not 0 < len(source) <= limit:
        raise ValueError("Generated artifact exceeds source bounds")
    artifact = workspace / "parser.py"
    if read_regular(artifact, limit) != starter:
        raise ValueError("Generated artifact requires the unchanged frozen starter")
    artifact.write_bytes(source)
    return {"artifact": "parser.py", "summary": answer["summary"]}


# Only one call is visible per process. No checks/expected answers are loaded.
RUNNER = '''import json, resource, sys
resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
if sys.argv[1:] == ["probe"]:
    print('{"ready": true}')
else:
    print("INFERRSI_ARTIFACT_READY", file=sys.stderr, flush=True)
    arguments = json.loads(sys.stdin.read())
    namespace = {"__name__": "submitted_parser"}
    with open("/work/parser.py", encoding="utf-8") as source:
        exec(compile(source.read(), "parser.py", "exec"), namespace)
    value = namespace["evaluate"](*arguments)
    print(json.dumps(value, allow_nan=False))
'''


def sandbox_command(work: Path, *, probe=False):
    """Linux system Python only; do not mount the caller's venv/home/repository."""
    bwrap = shutil.which("bwrap")
    python = Path("/usr/bin/python3").resolve()
    match = re.fullmatch(r"python(3\.\d+)", python.name)
    if sys.platform != "linux" or bwrap is None or not match or not python.is_file():
        raise SandboxUnavailable("Artifact replay requires Linux bubblewrap and system Python")
    stdlib = Path("/usr/lib") / ("python" + match[1])
    libraries = [path for path in (Path("/usr/lib/x86_64-linux-gnu"),
                                  Path("/usr/lib/aarch64-linux-gnu"), Path("/usr/lib64"))
                 if path.is_dir()]
    if not stdlib.is_dir() or not libraries:
        raise SandboxUnavailable("System Python runtime layout is unsupported")
    command = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--clearenv",
               "--ro-bind", str(python), "/usr/bin/python3",
               "--ro-bind", str(stdlib), str(stdlib)]
    for path in libraries:
        command += ["--ro-bind", str(path), str(path)]
    command += ["--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
                "--ro-bind", str(work), "/work", "--proc", "/proc", "--dev", "/dev",
                "--chdir", "/work", "--remount-ro", "/",
                "--", "/usr/bin/python3", "-I", "-S", "-B", "/work/runner.py"]
    if probe:
        command.append("probe")
    return command


def _invoke(work, arguments=None, *, probe=False):
    # Files, not PIPE capture: child output cannot grow parent memory. Hard
    # RLIMIT_FSIZE in the trusted runner bounds each retained stream to 64 KiB.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(
                sandbox_command(work, probe=probe), stdin=subprocess.PIPE,
                stdout=stdout, stderr=stderr, env={"PATH": "/usr/bin:/bin", "LANG": "C"},
                start_new_session=True,
            )
        except OSError as error:
            raise SandboxUnavailable("Cannot start artifact sandbox") from error
        timed_out = False
        try:
            process.communicate(json.dumps(arguments).encode(), timeout=5)
        except subprocess.TimeoutExpired:
            # The sandbox session is one process group; the Harness process domain ends it.
            _stop_tree(process)
            process.communicate()
            timed_out = True
        if not probe:
            stderr.seek(0)
            if not stderr.read(128).startswith(b"INFERRSI_ARTIFACT_READY\n"):
                raise SandboxUnavailable("Artifact worker did not establish its trusted boundary")
        if timed_out:
            return None
        stdout.seek(0)
        raw = stdout.read(65537)
        if process.returncode or len(raw) > 65536:
            return None
        try:
            return json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError):
            return None


def replay(source: bytes, calls: list) -> list:
    """Run every call against the artifact, each in its own sandbox process.

    A response is the decoded JSON value; None records a crash, timeout or oversized
    output. A failed preflight raises instead of falling back to the host.
    """
    with tempfile.TemporaryDirectory(prefix="skill-artifact-") as temporary:
        work = Path(temporary)
        (work / "runner.py").write_text(RUNNER, encoding="utf-8")
        (work / "parser.py").write_bytes(source)
        if _invoke(work, probe=True) != {"ready": True}:
            raise SandboxUnavailable("Artifact sandbox preflight failed; no host fallback")
        return [_invoke(work, call) for call in calls]
