"""Standalone Apps knowledge reads use fixed Bundle revisions and source authority."""

from hashlib import sha256

import pytest

from inferrsi.apps.knowledge import knowledge_reader, read
from inferrsi.knowledge.knowledge_operations import RevisionConflict
from inferrsi.knowledge.knowledge_store import KnowledgeStore

_IDENTITY = "inferrsi.common.skills.audit_h3_runtime_residency"


def test_knowledge_read_exposes_fixed_qualified_bundle_and_resource():
    reader = knowledge_reader()
    reference, revision, view = read(reader, _IDENTITY)
    assert reference == f"package:{_IDENTITY}"
    assert view["id"] == _IDENTITY
    assert view["revision"] == revision
    assert read(reader, reference, revision=revision) == (reference, revision, view)
    path = view["entrypoint"]
    contents = reader.resource(reference, path, revision=revision)
    assert sha256(contents).hexdigest() == view["resources"][path]
    with pytest.raises(RevisionConflict):
        read(reader, reference, revision="sha256:" + "0" * 64)


def test_checkout_read_scans_owners_once_with_a_read_only_store(tmp_path, monkeypatch):
    skills = tmp_path / "checkout" / "skills"
    skill = skills / "checkout-example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(
        b"---\nname: checkout-example\ndescription: Test entry\n---\nExample\n",
    )
    store_root = tmp_path / "store"
    store_root.mkdir()
    store = KnowledgeStore(store_root, skill_root=skills)
    reader = knowledge_reader(skill_root=skills, store_root=store_root)
    assert reader.store.read_only
    assert reader.store.database == store.database
    owners = reader.owners
    scans = []

    def count_owners():
        scans.append(None)
        return owners()

    monkeypatch.setattr(reader, "owners", count_owners)
    reference, revision, view = read(reader, "checkout-example", material=True)
    assert reference == "checkout:checkout-example"
    assert revision == view["revision"]
    assert view["content"].endswith("Example\n")
    assert len(scans) == 1
