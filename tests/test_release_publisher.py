"""Exercise the actual publisher shell against a tag/Release-only GitHub mock."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"
COMMIT = "1" * 40

MOCK_GH = r'''#!/usr/bin/env python
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
sys.stdout.reconfigure(newline="\n")
state_path = Path(os.environ["FAKE_GH_STATE"])
state = json.loads(state_path.read_text())
log = Path(os.environ["FAKE_GH_LOG"])


def record(operation, **data):
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"operation": operation, **data}) + "\n")


def save():
    state_path.write_text(json.dumps(state))


record("call", args=args)
repo = "repos/example/release"
commit = "1" * 40
tag = "v1.2.3"
if args == ["api", f"{repo}/compare/{commit}...main"]:
    print(json.dumps({"status": state.get("ancestry", "identical"),
                      "base_commit": {"sha": state.get("resolved", commit)}}))
elif args == ["api", "--paginate", "--slurp", f"{repo}/releases?per_page=100"]:
    releases = [{"tag_name": "v1.2.2", "draft": False, "prerelease": True}]
    if state.get("existing"):
        releases.append({"tag_name": tag, "draft": False, "prerelease": True})
    print(json.dumps([releases]))
elif args == ["api", "--method", "POST", f"{repo}/git/refs",
              "-f", f"ref=refs/tags/{tag}", "-f", f"sha={commit}"]:
    record("create_tag", ref=f"refs/tags/{tag}", sha=commit)
    state["tag_created"] = True
    save()
elif args[:3] == ["release", "create", tag]:
    if not state.get("tag_created") or "--draft" not in args or "--verify-tag" not in args:
        raise SystemExit("Release must be prepared as a draft on the existing tag")
    record("create_release")
    installers = [f"dist/inferrsi-{host}-plugin-v1.2.3.zip" for host in ("codex", "workswarm")]
    for installer in installers:
        if installer not in args:
            raise SystemExit("Release upload must include both versioned host installers")
    if "--generate-notes" not in args or "--notes-file" not in args:
        raise SystemExit("Keep the GitHub changelog and read summary as a file")
    notes = Path(args[args.index("--notes-file") + 1]).read_text(encoding="utf-8")
    if "Source: https://github.com/example/release/tree/" + commit not in notes:
        raise SystemExit("Release notes must retain exact source provenance")
    state["release_notes"] = notes
    save()
    if (state.get("ai_summary") and not state.get("failed_notes_download")
            and len(state["ai_summary"].encode("utf-8")) <= 20000):
        if "## AI-generated summary" not in notes or state["ai_summary"] not in notes:
            raise SystemExit("Missing AI summary")
    elif "## AI-generated summary" in notes:
        raise SystemExit("Missing AI output must fall back to ordinary release notes")
    for installer in installers:
        record("upload_plugin", name=Path(installer).name)
    state["assets"] = ["inferrsi-1.2.3.whl", "inferrsi-1.2.3.tar.gz", "SHA256SUMS",
                       *[Path(installer).name for installer in installers]]
    if state.get("missing_asset"):
        state["assets"].remove(f"inferrsi-{state['missing_asset']}-plugin-v1.2.3.zip")
    save()
elif args == ["release", "view", tag, "--json", "assets", "--jq", ".assets[].name"]:
    print("\n".join(state["assets"]))
elif args == ["api", f"{repo}/git/ref/tags/{tag}", "--jq", ".object.sha"]:
    print("2" * 40 if state.get("moved") else commit)
elif args[:3] == ["release", "edit", tag] and "--draft=false" in args:
    record("publish_release")
else:
    raise SystemExit("Forbidden operation: publisher may only create tags and publish Releases")
'''


def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_write_token_is_confined_to_a_no_checkout_publisher():
    config = workflow()
    assert config["permissions"] == {"contents": "read", "pull-requests": "none"}
    for name, job in config["jobs"].items():
        if name == "notes":
            assert job["permissions"] == {"contents": "read", "pull-requests": "none"}
            assert job["continue-on-error"] is True
            continue
        if name != "publish":
            assert not job.get("permissions")
            continue
        assert job["permissions"] == {"contents": "write", "pull-requests": "none"}
        actions = [step["uses"] for step in job["steps"] if "uses" in step]
        assert actions == ["actions/download-artifact@v4", "actions/download-artifact@v4"]
        assert "OPENAI_API_KEY" not in json.dumps(job)


def test_optional_notes_failure_cannot_skip_an_otherwise_valid_publication():
    job = workflow()["jobs"]["publish"]
    assert job["needs"] == ["plan", "build", "notes"]
    assert "always() && !cancelled()" in job["if"]
    assert "needs.build.result == 'success'" in job["if"]
    assert "needs.notes.result" not in job["if"]
    optional = [step for step in job["steps"]
                if step.get("with", {}).get("name") == "release-notes"]
    assert len(optional) == 1
    assert optional[0]["continue-on-error"] is True


@pytest.fixture
def publisher(tmp_path, request):
    release_tag = getattr(request, "param", "v1.2.3")
    previous_tag = "v0.1.10" if release_tag == "v0.2.0" else "v1.2.2"
    bash = shutil.which("bash")
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "")) / "Git/bin/bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    if not bash or not shutil.which("jq"):
        pytest.skip("Publisher shell integration requires bash and jq")
    mock_bin = tmp_path / "bin"
    mock_bin.mkdir()
    gh = mock_bin / "gh"
    mock = MOCK_GH.replace("1.2.3", release_tag[1:]).replace("v1.2.2", previous_tag)
    if release_tag == "v0.2.0":
        mock = mock.replace("## AI-generated summary", "## Reviewed v0.2.0 highlights")
        mock = mock.replace('record("create_release")',
                            'assert args[args.index("--notes-start-tag") + 1] == "v0.1.1"\n'
                            '    record("create_release")')
    gh.write_text(mock, encoding="utf-8", newline="\n")
    gh.chmod(0o755)
    dist = tmp_path / "dist"
    dist.mkdir()
    checksums = []
    for name in ("inferrsi-1.2.3.whl", "inferrsi-1.2.3.tar.gz",
                 "inferrsi-codex-plugin-v1.2.3.zip", "inferrsi-workswarm-plugin-v1.2.3.zip"):
        (dist / name.replace("1.2.3", release_tag[1:])).write_bytes(b"synthetic package")
        checksums.append(hashlib.sha256(b"synthetic package").hexdigest() + "  " +
                         name.replace("1.2.3", release_tag[1:]))
    (dist / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8", newline="\n")
    blocks = []
    for step in workflow()["jobs"]["publish"]["steps"]:
        if "run" in step:
            directory = step.get("working-directory", ".")
            blocks.append(f"(cd {directory}\n{step['run']})")
    script = tmp_path / "publish.sh"
    # Match the Linux runner's LF output when using the native Windows jq binary.
    shim = 'jq() { command jq --binary "$@"; }\n' if os.name == "nt" else ""
    script.write_text("set -euo pipefail\n" + shim + "\n".join(blocks),
                      encoding="utf-8", newline="\n")

    def run(state=None, **inputs):
        if state and state.get("ai_summary"):
            (tmp_path / "notes").mkdir()
            (tmp_path / "notes/ai-notes.md").write_text(state["ai_summary"], encoding="utf-8")
        if state and state.get("corrupt_zip"):
            (dist / f"inferrsi-{state['corrupt_zip']}-plugin-v1.2.3.zip").write_bytes(
                b"changed installer")
        if state and state.get("absent_zip"):
            (dist / f"inferrsi-{state['absent_zip']}-plugin-v1.2.3.zip").unlink()
        state_path, log_path = tmp_path / "state.json", tmp_path / "calls.jsonl"
        state_path.write_text(json.dumps(state or {}), encoding="utf-8")
        env = dict(os.environ)
        env.update(PATH=str(mock_bin) + os.pathsep + env["PATH"],
                   FAKE_GH_STATE=str(state_path), FAKE_GH_LOG=str(log_path),
                   RUNNER_TEMP=str(tmp_path),
                   GH_TOKEN="fake-no-access", GH_REPO="example/release", DEFAULT_BRANCH="main",
                   RELEASE_TAG=release_tag, RELEASE_COMMIT=COMMIT, PREVIOUS_TAG=previous_tag,
                   PRERELEASE="true")
        env.update(inputs)
        env["AI_NOTES_DOWNLOADED"] = (
            "failure" if state and state.get("failed_notes_download") else
            "success" if state and state.get("ai_summary") else "skipped"
        )
        result = subprocess.run([bash, str(script)], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=30)
        entries = [json.loads(line) for line in log_path.read_text().splitlines()] if (
            log_path.exists()) else []
        mutations = [entry for entry in entries if entry["operation"] != "call"]
        return result, mutations

    return run


def test_publisher_only_creates_a_tag_and_publishes_its_release(publisher):
    result, mutations = publisher()
    assert result.returncode == 0, result.stderr
    assert mutations == [
        {"operation": "create_tag", "ref": "refs/tags/v1.2.3", "sha": COMMIT},
        {"operation": "create_release"},
        {"operation": "upload_plugin", "name": "inferrsi-codex-plugin-v1.2.3.zip"},
        {"operation": "upload_plugin", "name": "inferrsi-workswarm-plugin-v1.2.3.zip"},
        {"operation": "publish_release"},
    ]


def test_ai_notes_are_text_and_keep_the_publication_mutation_boundary(publisher, tmp_path):
    # Treat model output as data even if it contains shell syntax.
    summary = "Features: a new command. `touch should-not-exist` $(touch should-not-exist)"
    result, mutations = publisher({"ai_summary": summary})
    assert result.returncode == 0, result.stderr
    assert [entry["operation"] for entry in mutations] == [
        "create_tag", "create_release", "upload_plugin", "upload_plugin", "publish_release",
    ]
    assert not (tmp_path / "should-not-exist").exists()


@pytest.mark.parametrize("state", [
    {"ai_summary": "A partial download", "failed_notes_download": True},
    {"ai_summary": "x" * 20001},
])
def test_unusable_notes_artifact_does_not_block_ordinary_publication(publisher, state):
    result, mutations = publisher(state)
    assert result.returncode == 0, result.stderr
    assert mutations[-1] == {"operation": "publish_release"}


@pytest.mark.parametrize("inputs", [
    {"RELEASE_TAG": "main"}, {"RELEASE_TAG": "refs/heads/main"},
    {"RELEASE_TAG": "--help"}, {"RELEASE_TAG": "v1.2.3\nrefs/heads/main"},
    {"RELEASE_COMMIT": "main"}, {"RELEASE_COMMIT": "1" * 39},
    {"PREVIOUS_TAG": "refs/heads/main"}, {"PRERELEASE": "yes"},
])
def test_invalid_publication_inputs_cannot_write(publisher, inputs):
    result, mutations = publisher(**inputs)
    assert result.returncode != 0
    assert mutations == []


@pytest.mark.parametrize("state", [
    {"existing": True}, {"ancestry": "diverged"}, {"resolved": "2" * 40},
    *[{problem: host} for problem in ("corrupt_zip", "absent_zip")
      for host in ("codex", "workswarm")],
])
def test_conflicting_or_unverified_source_cannot_write(publisher, state):
    result, mutations = publisher(state)
    assert result.returncode != 0
    assert mutations == []


@pytest.mark.parametrize("state", [
    {"moved": True}, {"missing_asset": "codex"}, {"missing_asset": "workswarm"},
])
def test_incomplete_or_retargeted_release_cannot_be_published(publisher, state):
    result, mutations = publisher(state)
    assert result.returncode != 0
    assert [entry["operation"] for entry in mutations] == [
        "create_tag", "create_release", "upload_plugin", "upload_plugin",
    ]


@pytest.mark.parametrize("publisher", ["v0.2.0"], indirect=True)
def test_one_off_publishes_reviewed_notes_with_historical_baseline(publisher, tmp_path):
    result, mutations = publisher({"ai_summary": "Reviewed highlights since v0.1.1"},
                                  PRERELEASE="false")
    assert result.returncode == 0, result.stderr
    assert mutations[-1] == {"operation": "publish_release"}
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    edits = [entry["args"] for entry in calls if entry.get("operation") == "call"
             and entry["args"][:2] == ["release", "edit"]]
    assert "--prerelease=false" in edits[-1] and "--latest" in edits[-1]


@pytest.mark.parametrize("publisher", ["v0.2.0"], indirect=True)
@pytest.mark.parametrize("state", [None, {"ai_summary": "x", "failed_notes_download": True},
                                   {"ai_summary": "x" * 20001}])
def test_one_off_missing_reviewed_notes_blocks_before_tag(publisher, state):
    result, mutations = publisher(state, PRERELEASE="false")
    assert result.returncode != 0
    assert mutations == []


def test_one_off_dispatch_is_opt_in_and_schedule_cannot_select_it():
    config = workflow()
    trigger = config.get("on", config.get(True))
    assert trigger["workflow_dispatch"]["inputs"]["release_v020"]["default"] is False
    step = next(step for step in config["jobs"]["plan"]["steps"] if step.get("id") == "plan")
    assert "github.event_name == 'workflow_dispatch' && inputs.release_v020" in (
        step["env"]["RELEASE_V020"])
    assert '"${options[@]}"' in step["run"]
