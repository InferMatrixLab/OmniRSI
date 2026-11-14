import io
import subprocess
import tarfile

import pytest

from tools.check_release import check_sdist, check_versions, plan_release, release_version


@pytest.mark.parametrize("tag", ["v0.1.0", "v1.2.3", "v10.20.30"])
def test_stable_release_versions(tag):
    check_versions(tag, tag[1:], tag[1:])


@pytest.mark.parametrize("tag", [
    "1.2.3", "v01.2.3", "v1.2", "v1.2.3rc1", "v1.2.3+local", "v1.2.3\n", "--help",
])
def test_reject_ambiguous_and_nonstable_tags(tag):
    with pytest.raises(ValueError):
        release_version(tag)


@pytest.mark.parametrize("distribution,runtime", [("1.2.4", "1.2.3"), ("1.2.3", "1.2.4")])
def test_reject_inconsistent_distribution_or_runtime(distribution, runtime):
    with pytest.raises(ValueError, match="agree exactly"):
        check_versions("v1.2.3", distribution, runtime)


@pytest.fixture
def release_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        return subprocess.check_output(["git", *args], text=True, stderr=subprocess.PIPE).strip()

    git("init", "-b", "main")
    git("config", "user.name", "Release Test")
    git("config", "user.email", "release@example.test")
    git("commit", "--allow-empty", "-m", "initial")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    return git


def published(tag="v0.1.0", draft=False, prerelease=False):
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease}


def change_source(git):
    from pathlib import Path

    Path("source.txt").write_text("changed", encoding="utf-8")
    git("add", "source.txt")
    git("commit", "-m", "change")
    git("update-ref", "refs/remotes/origin/main", "HEAD")


def test_bootstrap_without_existing_tags(release_repo):
    plan = plan_release("main", [], "0.1.0")
    assert plan == {"release": "true", "tag": "v0.1.0",
                    "version": "0.1.0", "prerelease": "true",
                    "commit": release_repo("rev-parse", "HEAD"),
                    "previous_tag": ""}


@pytest.mark.parametrize("empty_commit", [False, True])
def test_unchanged_tree_skips_even_with_new_commit(release_repo, empty_commit):
    release_repo("tag", "v0.1.0")
    if empty_commit:
        release_repo("commit", "--allow-empty", "-m", "empty")
        release_repo("update-ref", "refs/remotes/origin/main", "HEAD")
    assert plan_release("main", [published()], "0.1.0") == {"release": "false"}


def test_changed_tree_uses_semantic_patch_order_and_ignores_nonstable_tags(release_repo):
    release_repo("tag", "v0.1.9")
    release_repo("tag", "v0.1.10")
    release_repo("tag", "v9.0.0rc1")
    release_repo("tag", "arbitrary")
    change_source(release_repo)
    plan = plan_release("main", [published("v0.1.9"), published("v0.1.10")], "0.1.0")
    assert plan["tag"] == "v0.1.11"
    assert plan["previous_tag"] == "v0.1.10"


def test_failed_reserved_tag_or_draft_does_not_suppress_retry(release_repo):
    release_repo("tag", "v0.1.0")
    release_repo("tag", "v0.1.1")
    change_source(release_repo)
    releases = [published(), published("v0.1.1", draft=True)]
    assert plan_release("main", releases, "0.1.0")["tag"] == "v0.1.2"


def test_first_attempt_failure_allocates_new_version_instead_of_overwriting(release_repo):
    release_repo("tag", "v0.1.0")
    plan = plan_release("main", [published(draft=True)], "0.1.0")
    assert plan["tag"] == "v0.1.1" and plan["previous_tag"] == ""
    assert plan["version"] == "0.1.1"
    assert plan["commit"] == release_repo("rev-parse", "refs/tags/v0.1.0")


def test_draft_without_a_remote_tag_still_reserves_its_version(release_repo):
    assert plan_release("main", [published(draft=True)], "0.1.0")["tag"] == "v0.1.1"


def test_published_developer_preview_is_a_successful_no_change_baseline(release_repo):
    release_repo("tag", "v0.1.0")
    assert plan_release("main", [published(prerelease=True)], "0.1.0") == {"release": "false"}
    change_source(release_repo)
    plan = plan_release("main", [published(prerelease=True)], "0.2.0")
    assert plan["tag"] == "v0.2.0" and plan["previous_tag"] == "v0.1.0"


def test_explicit_stable_channel_and_invalid_channel_configuration(release_repo):
    assert plan_release("main", [], "1.0.0", False)["prerelease"] == "false"
    with pytest.raises(ValueError, match="boolean"):
        plan_release("main", [], "1.0.0", "false")


def test_missing_published_tag_fails_instead_of_publishing_unverified_source(release_repo):
    with pytest.raises(subprocess.CalledProcessError):
        plan_release("main", [published()], "0.1.0")


def test_reject_published_source_outside_default_history(release_repo):
    release_repo("checkout", "-b", "unmerged")
    release_repo("commit", "--allow-empty", "-m", "unmerged")
    release_repo("tag", "v0.1.0")
    with pytest.raises(subprocess.CalledProcessError):
        plan_release("main", [published()], "0.1.0")


@pytest.mark.parametrize("name,allowed", [
    ("src/inferrsi/knowledge/data/example.json", True), ("README.md", True),
    ("evidence/run/results.json", False), (".github/workflows/release.yml", False),
    ("../outside", False),
])
def test_sdist_boundary(tmp_path, name, allowed):
    path = tmp_path / "package.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        entry = tarfile.TarInfo("inferrsi-0.1.0/" + name)
        archive.addfile(entry, io.BytesIO(b""))
    if allowed:
        check_sdist(path)
    else:
        with pytest.raises(ValueError):
            check_sdist(path)


def test_one_off_v020_preserves_default_policy(release_repo):
    release_repo("tag", "v0.1.1")
    change_source(release_repo)
    releases = [published("v0.1.1", prerelease=True)]
    result = plan_release("main", releases, "0.1.0", release_v020=True)
    assert result["tag"] == "v0.2.0"
    assert result["prerelease"] == "false"
    assert result["previous_tag"] == "v0.1.1"
    assert plan_release("main", releases, "0.1.0")["tag"] == "v0.1.2"
    release_repo("tag", "v0.2.0")
    release_repo("commit", "--allow-empty", "-m", "later")
    from pathlib import Path
    Path("later.txt").write_text("later")
    release_repo("add", "later.txt")
    release_repo("commit", "-m", "changed")
    release_repo("update-ref", "refs/remotes/origin/main", "HEAD")
    following = plan_release("main", releases + [published("v0.2.0")], "0.1.0")
    assert following["tag"] == "v0.2.1" and following["prerelease"] == "true"


@pytest.mark.parametrize("tag", ["v0.2.0", "v0.2.1", "v1.0.0"])
@pytest.mark.parametrize("reservation", ["tag", "draft", "published"])
def test_one_off_rejects_reserved_or_obsolete_target(release_repo, tag, reservation):
    release_repo("tag", "v0.1.1")
    change_source(release_repo)
    releases = [published("v0.1.1")]
    if reservation == "tag":
        release_repo("tag", tag)
    else:
        releases.append(published(tag, draft=reservation == "draft"))
    with pytest.raises(ValueError, match="unused target"):
        plan_release("main", releases, "0.1.0", release_v020=True)


def test_one_off_can_promote_unchanged_preview_tree(release_repo):
    release_repo("tag", "v0.1.1")
    result = plan_release("main", [published("v0.1.1")], "0.1.0", release_v020=True)
    assert result["tag"] == "v0.2.0" and result["prerelease"] == "false"


def test_one_off_requires_notes_baseline(release_repo):
    with pytest.raises(subprocess.CalledProcessError):
        plan_release("main", [], "0.1.0", release_v020=True)
