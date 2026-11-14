"""Compose the standalone Apps knowledge operations through Knowledge ports."""

from pathlib import Path

from inferrsi.knowledge.knowledge_operations import KnowledgeReader
from inferrsi.knowledge.knowledge_store import KnowledgeStore


def knowledge_reader(
    *, skill_root: Path | None = None, store_root: Path | None = None,
) -> KnowledgeReader:
    """Select a Knowledge reader without enabling store writes."""
    store = (
        KnowledgeStore(store_root, skill_root=skill_root, read_only=True)
        if store_root is not None
        else None
    )
    return KnowledgeReader(skill_root=skill_root, store=store)


def read(
    reader: KnowledgeReader, reference: str, *, revision: str | None = None, material: bool = False,
) -> tuple[str, str, dict]:
    """Return the qualified reference, actual revision, and public Bundle view."""
    qualified, bundle = reader.read_resolved(reference, revision=revision)
    view = bundle.view(material=material)
    return qualified, view["revision"], view
