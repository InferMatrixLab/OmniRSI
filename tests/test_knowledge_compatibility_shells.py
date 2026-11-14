# K01: root Knowledge names are static re-exports of the moved implementations.

from __future__ import annotations

import hashlib
import importlib
import os
import subprocess
import sys

import pytest

from tests._gate_policy_support import REPO, ROOT, blocker_kinds, first_wave_report, load_manifest

MANIFEST = load_manifest()
LEAF = MANIFEST["technical_leaf"]
LEAF_NAMES = MANIFEST["catalog_two_routes"]["neutral_leaf_export"]["symbols"]
MOVED = [item for item in MANIFEST["compatibility_pairs"] if "exports" in item]


@pytest.mark.parametrize("item", MOVED, ids=lambda item: item["importer"])
def test_root_names_are_the_moved_objects(item):
    # K01-1: identity carries signatures, defaults, exception types and Path behavior.
    root = importlib.import_module(f"inferrsi.{item['importer']}")
    moved = importlib.import_module(item["target"])
    names = [*item["exports"]["public"], *item["exports"]["private_used"]]
    for name in names:
        assert getattr(root, name) is getattr(moved, name), name
    extra = LEAF_NAMES if item["importer"] == "catalog" else []
    # REPO_TYPES is listed on both catalog routes; __all__ names it once.
    assert len(root.__all__) == len(set(root.__all__))
    assert set(root.__all__) == {*names, *extra}


def test_catalog_routes_share_one_repo_types_tuple():
    from inferrsi import _repo_pack_data, catalog
    from inferrsi.knowledge import catalog as knowledge_catalog

    assert catalog.REPO_TYPES is _repo_pack_data.REPO_TYPES is knowledge_catalog.REPO_TYPES
    assert catalog.repository_pack is _repo_pack_data.repository_pack
    assert not hasattr(knowledge_catalog, "repository_pack")
    assert knowledge_catalog.KNOWLEDGE_SCOPES["contribution"] == (
        *_repo_pack_data.REPO_TYPES, "vllm_ascend", "inferrsi"
    )


def test_data_leaf_holds_the_recorded_fragment_and_locked_hash():
    data = (ROOT / "_repo_pack_data.py").read_bytes()
    assert hashlib.sha256(data).hexdigest() == LEAF["file_sha256"]
    # The body in the file today, and the P0b provenance text it came from.
    current = LEAF["current_fragment"]
    assert current["source"] + "\n" in data.decode("utf-8")
    assert hashlib.sha256((current["source"] + "\n").encode("utf-8")).hexdigest() == (
        current["fragment_sha256"]
    )
    revision = LEAF["resource_path_revision"]
    original = LEAF["original_fragment"]["source"]
    assert original.replace(revision["old_line"], revision["new_line"]) == current["source"]
    assert revision["new_file_sha256"] == LEAF["file_sha256"]


def _fresh(script: str) -> None:
    environment = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=environment,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_package_init_and_infra_inspection_load_no_knowledge_body():
    _fresh(
        "import sys\n"
        "import inferrsi.knowledge\n"
        "assert not [m for m in sys.modules if m.startswith('inferrsi.knowledge.')]\n"
        "import inferrsi.infra.inspection\n"
        "assert 'inferrsi.catalog' not in sys.modules\n"
        "assert not [m for m in sys.modules if m.startswith('inferrsi.knowledge.')]\n"
        "assert 'inferrsi._repo_pack_data' in sys.modules\n"
    )


def test_knowledge_closure_is_isolated_while_overall_stays_false():
    # K01-5: the Knowledge package exists and its closure is clean, but the
    # allowlist and harness -> model/infra remain. S01 removed the residual.
    # After #338, runtime resource loaders keep new_core_closure_isolated
    # false; that is not a Knowledge-closure regression.
    report = first_wave_report()
    assert report.violations == ()
    assert "absent: knowledge/" not in report.blockers
    assert {
        "allowlist",
        "harness reaches model/infra",
        "runtime resource loaders",
    } <= blocker_kinds(report)
    assert "residual" not in blocker_kinds(report)
    assert not report.new_core_closure_isolated
    assert not report.isolation_complete
