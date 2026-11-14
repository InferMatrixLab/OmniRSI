from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests._gate_policy_support import (
    IsolationReport,
    blocker_kinds,
    exact_pairs,
    first_wave_report,
    load_manifest,
    shell_sources,
)

MANIFEST = load_manifest()
PAIRS = exact_pairs(MANIFEST)
SOURCES = shell_sources(MANIFEST)
LEAF = MANIFEST["technical_leaf"]["module"]
EMPTY_ALLOWLIST = {**MANIFEST, "compatibility_pairs": []}
# The future neutral leaf, built from the manifest fragment and allowed imports.
LEAF_SOURCE = (
    '"""Raw repository-pack data."""\n\n'
    "from __future__ import annotations\n\n"
    "import json\n"
    "from importlib.resources import files\n"
    "from typing import Any\n\n"
    f"{MANIFEST['technical_leaf']['original_fragment']['source']}\n"
)
# Module paths assumed for synthetic graphs only; the gate never infers them.
SYNTHETIC_H_ENTRIES = (
    "inferrsi.harness.api",
    "inferrsi.harness.execution_service",
    "inferrsi.harness.run_store",
)
# Every first-wave trigger is cleared: no allowlist and no legacy mix.
CLEARED_TREE = {
    "__init__.py": "",
    "_repo_pack_data.py": LEAF_SOURCE,
    "knowledge/__init__.py": "",
    "knowledge/catalog.py": "from .._repo_pack_data import REPO_TYPES\n",
    "benchmark/__init__.py": "",
    "apps/__init__.py": "",
    "apps/main.py": "from inferrsi.knowledge import catalog\nfrom inferrsi.harness import api\n",
    "harness/__init__.py": "",
    "harness/api.py": "VALUE = 1\n",
}

def _shell(importer: str) -> str:
    """A shape-valid static shell generated from the manifest export lists."""
    lines = ['"""Compatibility shell."""', "", "from __future__ import annotations", ""]
    seen: set[str] = set()
    for target, names in sorted(SOURCES[importer].items()):
        for name in sorted(names - seen):
            lines.append(f"from {target} import {name} as {name}")
            seen.add(name)
    quoted = ", ".join(f'"{name}"' for name in sorted(seen))
    return "\n".join([*lines, "", f"__all__ = [{quoted}]", ""])


def _k01_tree() -> dict[str, str]:
    """A K01-shaped tree that follows every first-wave rule."""
    files = {
        "__init__.py": "",
        "_repo_pack_data.py": LEAF_SOURCE,
        "_json_v1.py": "import json\n",
        "knowledge/__init__.py": '"""Knowledge."""\n',
        # K.catalog -> the data leaf is intra-closure, not a tenth pair.
        "knowledge/catalog.py": "from .. import _repo_pack_data\nfrom . import _source_snapshot\n",
        "knowledge/knowledge_operations.py": "from inferrsi import _json_v1\n",
        "infra/__init__.py": "",
        "infra/inspection.py": "from .._repo_pack_data import repository_pack\n",
        "harness/__init__.py": "",
        "harness/api.py": "VALUE = 1\n",
        "benchmark/__init__.py": "",
        "apps/__init__.py": "",
        "apps/main.py": "from inferrsi.knowledge import catalog\n",
    }
    for importer in SOURCES:
        name = importer.removeprefix("inferrsi.")
        files[f"{name}.py"] = _shell(importer)
        files.setdefault(f"knowledge/{name}.py", '"""Moved body."""\n')
    return files


def _report(
    changes: dict[str, str | None],
    manifest: dict[str, Any] = MANIFEST,
    h_entries: tuple[str, ...] = SYNTHETIC_H_ENTRIES,
    base: dict[str, str] | None = None,
) -> IsolationReport:
    """Scan a temporary tree; a ``None`` change drops that file."""
    files = {**(_k01_tree() if base is None else base), **changes}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for relative, source in files.items():
            if source is None:
                continue
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        return first_wave_report(root, manifest, h_entries)


def _violated(report: IsolationReport, *parts: str) -> bool:
    return any(all(part in message for part in parts) for message in report.violations)


class ManifestTest(unittest.TestCase):
    def test_allowlist_is_exactly_nine_pairs(self) -> None:
        leaves = (
            "catalog",
            "_source_snapshot",
            "skill_catalog",
            "_skill_selection",
            "research",
            "knowledge_store",
            "knowledge_operations",
        )
        same_named = {(f"inferrsi.{n}", f"inferrsi.knowledge.{n}") for n in leaves}
        leaf_routes = {("inferrsi.catalog", LEAF), ("inferrsi.infra.inspection", LEAF)}
        self.assertEqual(len(MANIFEST["compatibility_pairs"]), 9)
        self.assertEqual(PAIRS, same_named | leaf_routes)

    def test_catalog_routes_keep_repository_pack_out_of_knowledge(self) -> None:
        catalog = SOURCES["inferrsi.catalog"]
        self.assertNotIn("repository_pack", catalog["inferrsi.knowledge.catalog"])
        self.assertEqual(catalog[LEAF], {"REPO_TYPES", "repository_pack"})

    def test_fragment_hash_matches_recorded_source(self) -> None:
        # The recorded hash covers the fragment text plus its final newline.
        fragment = MANIFEST["technical_leaf"]["original_fragment"]
        text = fragment["source"] + "\n"
        self.assertEqual(
            hashlib.sha256(text.encode("utf-8")).hexdigest(), fragment["fragment_sha256"]
        )


class AllowlistTest(unittest.TestCase):
    """A01-1: nine exact (importer, target) pairs and nothing else."""

    def test_nine_exact_pairs_pass(self) -> None:
        report = _report({})
        self.assertEqual(report.violations, ())
        self.assertNotIn(("inferrsi.knowledge.catalog", LEAF), PAIRS)

    def test_adjacent_knowledge_target_fails(self) -> None:
        shell = _shell("inferrsi.catalog")
        shell += "from inferrsi.knowledge.catalog_extra import VALUE as VALUE\n"
        report = _report({"catalog.py": shell, "knowledge/catalog_extra.py": "VALUE = 1\n"})
        self.assertTrue(_violated(report, "catalog.py imports inferrsi.knowledge"))
        self.assertTrue(
            _violated(report, "unlisted import from inferrsi.knowledge.catalog_extra")
        )

    def test_second_importer_fails(self) -> None:
        cases = (
            (
                "evaluation.py",
                "from inferrsi.knowledge.catalog import search_knowledge\n",
                "evaluation.py imports inferrsi.knowledge",
            ),
            (
                "infra/inspection.py",
                "from inferrsi.knowledge.catalog import REPO_TYPES\n",
                "infra/inspection.py imports inferrsi.knowledge",
            ),
            (
                "evaluation.py",
                "from inferrsi._repo_pack_data import repository_pack\n",
                "inferrsi.evaluation is a second importer of inferrsi._repo_pack_data",
            ),
            (
                "harness/api.py",
                "from inferrsi import _repo_pack_data\n",
                "inferrsi.harness.api is a second importer of inferrsi._repo_pack_data",
            ),
        )
        for path, source, expected in cases:
            with self.subTest(path=path, source=source):
                report = _report({path: source})
                self.assertTrue(_violated(report, expected), report.violations)

    def test_only_declared_infra_importer_may_use_the_leaf(self) -> None:
        # D020: infra -> the data leaf passes, and only infra.inspection is
        # the declared infra importer of that leaf.
        report = _report({})
        self.assertEqual(report.violations, ())
        infra_leaf = {
            (importer, target)
            for importer, target in PAIRS
            if importer.startswith("inferrsi.infra.") and target == LEAF
        }
        self.assertEqual(infra_leaf, {("inferrsi.infra.inspection", LEAF)})
        report = _report({"infra/other.py": "from .._repo_pack_data import REPO_TYPES\n"})
        self.assertTrue(
            _violated(
                report,
                "inferrsi.infra.other is a second importer of inferrsi._repo_pack_data",
            ),
            report.violations,
        )


SHELL_DEFECTS = (
    ("def research_knowledge():\n    return None\n", "function or class body research_knowledge"),
    ("class Shim:\n    pass\n", "function or class body Shim"),
    ("def __getattr__(name):\n    return name\n", "defines __getattr__"),
    ("import sys\nsys.modules[__name__] = sys\n", "touches sys.modules"),
    ("import importlib\nimportlib.import_module('inferrsi.knowledge')\n", "dynamic import"),
    ("__import__('inferrsi.knowledge.research')\n", "dynamic import"),
    ("from inferrsi.knowledge.research import *\n", "star import"),
    (
        "from inferrsi.knowledge.research import research_knowledge as found\n",
        "aliases research_knowledge as found",
    ),
    (
        "from inferrsi.knowledge.research import helper as helper\n",
        "unlisted name inferrsi.knowledge.research.helper",
    ),
    (
        "from inferrsi.knowledge.catalog import get_knowledge as get_knowledge\n",
        "unlisted import from inferrsi.knowledge.catalog",
    ),
    ("import json\n", "unlisted import of json"),
    ("VALUE = 1\n", "module-level assignment"),
    ("__all__ = ['research_knowledge'] + []\n", "__all__ is not a literal list"),
    ("__all__ = ('research_knowledge',)\n", "__all__ is not a literal list"),
)


class ShellShapeTest(unittest.TestCase):
    """A01-2: a shell is docstring, future import, listed imports, literal __all__."""

    def test_illegal_shell_shapes_fail(self) -> None:
        for snippet, expected in SHELL_DEFECTS:
            with self.subTest(snippet=snippet):
                report = _report({"research.py": _shell("inferrsi.research") + snippet})
                self.assertTrue(
                    _violated(report, "inferrsi.research ", expected), report.violations
                )

    def test_old_leaf_keeping_a_body_beside_its_k_module_fails(self) -> None:
        # The declared export edge makes root catalog a shell once K.catalog
        # exists, so a second copy of the body is rejected.
        report = _report(
            {
                "catalog.py": "def search_knowledge():\n    return []\n",
                "infra/inspection.py": "from ..catalog import search_knowledge\n",
            }
        )
        self.assertTrue(_violated(report, "inferrsi.catalog has a function or class body"))
        self.assertTrue(
            _violated(report, "inferrsi.infra.inspection reaches inferrsi.knowledge.catalog")
        )


class TransferGraphTest(unittest.TestCase):
    """Synthetic transfer graphs over absolute, relative, and package-name edges."""

    def test_knowledge_through_old_shell_fails(self) -> None:
        forms = (
            "from inferrsi.catalog import search_knowledge\n",
            "from .. import catalog\n",
            "from inferrsi import catalog\n",
            "import inferrsi.catalog\n",
        )
        for source in forms:
            with self.subTest(source=source):
                report = _report({"knowledge/research.py": source})
                self.assertTrue(
                    _violated(report, "inferrsi.knowledge.research reaches inferrsi.catalog"),
                    report.violations,
                )

    def test_apps_through_old_shell_fails(self) -> None:
        report = _report({"apps/main.py": "from inferrsi.knowledge_store import KnowledgeStore\n"})
        self.assertTrue(_violated(report, "inferrsi.apps.main reaches inferrsi.knowledge_store"))

    def test_data_leaf_accepts_only_listed_stdlib_imports(self) -> None:
        self.assertFalse(_violated(_report({}), LEAF))
        hidden = LEAF_SOURCE.replace(
            "    if repo_type", "    from inferrsi import catalog\n    if repo_type", 1
        )
        dynamic = LEAF_SOURCE.replace("import json\n", "import importlib\nimport json\n")
        dynamic += "\n\ndef _load(name):\n    return importlib.import_module(name)\n"
        cases = (
            (hidden, "imports inferrsi.catalog"),
            (LEAF_SOURCE.replace("import json\n", "import json\nimport inferrsi.infra\n"),
             "imports inferrsi.infra"),
            (LEAF_SOURCE.replace("import json\n", "import json\nfrom . import catalog\n"),
             "imports inferrsi.catalog"),
            (dynamic, "has a dynamic import"),
            (LEAF_SOURCE.replace("import Any", "import Any, Dict"), "imports typing.Dict"),
            (LEAF_SOURCE + "\nEXTRA = 1\n", "defines EXTRA at top level"),
        )
        for source, expected in cases:
            with self.subTest(expected=expected):
                report = _report({"_repo_pack_data.py": source})
                self.assertTrue(_violated(report, f"{LEAF} {expected}"), report.violations)

    def test_infra_through_root_catalog_fails(self) -> None:
        report = _report({"infra/inspection.py": "from ..catalog import repository_pack\n"})
        self.assertIn(
            "inferrsi.infra.inspection reaches inferrsi.knowledge.catalog: "
            "inferrsi.infra.inspection -> inferrsi.catalog -> inferrsi.knowledge.catalog",
            report.violations,
        )

    def test_infra_reaching_harness_benchmark_or_apps_fails(self) -> None:
        cases = (
            ("from inferrsi.harness import api\n", "inferrsi.harness.api"),
            ("import inferrsi.benchmark\n", "inferrsi.benchmark"),
            ("import inferrsi.apps.main\n", "inferrsi.apps.main"),
        )
        for source, target in cases:
            with self.subTest(source=source):
                report = _report({"infra/probe.py": source})
                self.assertTrue(_violated(report, f"inferrsi.infra.probe reaches {target}"))

    def test_undeclared_dynamic_edge_fails_closed(self) -> None:
        source = "import importlib\n\n\ndef load():\n    return importlib.import_module('x')\n"
        for path, module in (
            ("knowledge/research.py", "inferrsi.knowledge.research"),
            ("infra/inspection.py", "inferrsi.infra.inspection"),
        ):
            with self.subTest(path=path):
                report = _report({path: source})
                self.assertTrue(
                    _violated(report, f"{module} reaches an undeclared dynamic import in {module}")
                )


class HarnessEntryTest(unittest.TestCase):
    """A01-3: no harness module reaches Knowledge through an old root shell."""

    def test_new_h_entry_through_old_shell_fails(self) -> None:
        cases = (
            (
                "harness/api.py",
                "from inferrsi.knowledge_operations import KnowledgeReader\n",
                "inferrsi.harness.api reaches inferrsi.knowledge_operations",
            ),
            (
                "harness/run_store/__init__.py",
                "from ...catalog import search_knowledge\n",
                "inferrsi.harness.run_store reaches inferrsi.catalog",
            ),
            (
                "harness/execution_service.py",
                "from inferrsi.research import research_knowledge\n",
                "inferrsi.harness.execution_service reaches inferrsi.research",
            ),
        )
        for path, source, expected in cases:
            with self.subTest(path=path):
                report = _report({path: source})
                self.assertTrue(_violated(report, expected), report.violations)

    def test_entry_labels_are_not_inferred_as_paths(self) -> None:
        # A guessed run_store path is not a new H entry just because the
        # label exists, but it is still a harness module: importing an old
        # Knowledge leaf is a violation.
        report = _report(
            {"harness/run_store/__init__.py": "from ...catalog import search_knowledge\n"},
            h_entries=("inferrsi.harness.api",),
        )
        self.assertTrue(
            _violated(report, "inferrsi.harness.run_store reaches inferrsi.catalog"),
            report.violations,
        )

    def test_non_entry_harness_module_cannot_reach_an_old_leaf(self) -> None:
        cases = (
            (
                "from ..knowledge_operations import KnowledgeReader\n",
                "inferrsi.harness.runner reaches inferrsi.knowledge_operations",
            ),
            (
                "from inferrsi.catalog import search_knowledge\n",
                "inferrsi.harness.runner reaches inferrsi.catalog",
            ),
        )
        for source, expected in cases:
            with self.subTest(expected=expected):
                report = _report({"harness/runner.py": source})
                self.assertTrue(_violated(report, expected), report.violations)

    def test_former_residual_edge_fails_for_its_importer_and_dependents(self) -> None:
        # The edge that S01 removed has no exemption left: the importer fails,
        # and so does every harness module that reaches the leaf through it.
        tree = {
            "harness/skill_evaluation.py": "from ..knowledge_operations import KnowledgeReader\n",
            "harness/improvement.py": "from . import skill_evaluation\n",
        }
        report = _report(tree)
        self.assertIn(
            "inferrsi.harness.skill_evaluation reaches inferrsi.knowledge_operations: "
            "inferrsi.harness.skill_evaluation -> inferrsi.knowledge_operations",
            report.violations,
        )
        self.assertIn(
            "inferrsi.harness.improvement reaches inferrsi.knowledge_operations: "
            "inferrsi.harness.improvement -> inferrsi.harness.skill_evaluation"
            " -> inferrsi.knowledge_operations",
            report.violations,
        )
        self.assertNotIn("residual", blocker_kinds(report))
        self.assertFalse(report.isolation_complete)
        entry = "from inferrsi.harness import skill_evaluation\n"
        granted = _report({**tree, "harness/api.py": entry})
        self.assertIn(
            "inferrsi.harness.api reaches inferrsi.knowledge_operations: inferrsi.harness.api"
            " -> inferrsi.harness.skill_evaluation -> inferrsi.knowledge_operations",
            granted.violations,
        )
        self.assertFalse(granted.new_core_closure_isolated)

    def test_submodule_import_traverses_parent_package_init(self) -> None:
        parent = {
            "bridge/__init__.py": "from inferrsi.knowledge_operations import KnowledgeReader\n",
            "bridge/safe.py": "VALUE = 1\n",
        }
        cases = (
            ("import inferrsi.bridge.safe\n", "import package.submodule"),
            ("from inferrsi.bridge import safe\n", "from package import submodule"),
        )
        for source, form in cases:
            with self.subTest(form=form):
                report = _report({**parent, "harness/api.py": source})
                self.assertTrue(
                    _violated(
                        report,
                        "inferrsi.harness.api reaches inferrsi.knowledge_operations",
                    ),
                    report.violations,
                )
                self.assertFalse(report.new_core_closure_isolated)

    def test_transitive_path_through_root_helpers_fails(self) -> None:
        report = _report(
            {
                "harness/runner.py": "from inferrsi import helper_a\n",
                "helper_a.py": "from inferrsi import helper_b\n",
                "helper_b.py": (
                    "from inferrsi.knowledge_operations import KnowledgeReader\n"
                ),
            }
        )
        self.assertIn(
            "inferrsi.harness.runner reaches inferrsi.knowledge_operations: "
            "inferrsi.harness.runner -> inferrsi.helper_a -> inferrsi.helper_b"
            " -> inferrsi.knowledge_operations",
            report.violations,
        )

    def test_old_leaf_detection_is_not_sort_dependent(self) -> None:
        # A second path to the same leaf is found whichever edge is walked first.
        direct = {
            "harness/skill_evaluation.py": (
                "from ..knowledge_operations import KnowledgeReader\n"
            )
        }
        for name in ("aa_bridge", "zz_bridge"):
            with self.subTest(name=name):
                report = _report(
                    {
                        **direct,
                        f"{name}.py": "from . import knowledge_operations\n",
                        "harness/runner.py": (
                            f"from . import skill_evaluation\nfrom .. import {name}\n"
                        ),
                    }
                )
                for module in ("runner", "skill_evaluation"):
                    self.assertTrue(
                        _violated(
                            report,
                            f"inferrsi.harness.{module} reaches inferrsi.knowledge_operations",
                        ),
                        report.violations,
                    )

    def test_h_entry_own_parent_init_is_walked(self) -> None:
        # Importing a new H entry executes its package init even when the
        # entry body has no imports.
        leak = {
            "harness/skill_evaluation.py": (
                "from ..knowledge_operations import KnowledgeReader\n"
            ),
            "harness/__init__.py": "from . import skill_evaluation\n",
        }
        expected = (
            "inferrsi.harness.api reaches inferrsi.knowledge_operations: "
            "inferrsi.harness.api -> inferrsi.harness -> "
            "inferrsi.harness.skill_evaluation -> inferrsi.knowledge_operations"
        )
        isolated = _report({**leak, "harness/api.py": "VALUE = 1\n"})
        self.assertIn(expected, isolated.violations)
        self.assertFalse(isolated.new_core_closure_isolated)
        sibling = _report(
            {
                **leak,
                "harness/util.py": "X = 1\n",
                "harness/api.py": "from . import util\n",
            }
        )
        self.assertIn(expected, sibling.violations)
        self.assertFalse(sibling.new_core_closure_isolated)

    def test_non_entry_own_parent_init_is_walked(self) -> None:
        # A non-entry with no imports of its own still reaches the old leaf
        # through the package init, and fails like an entry does.
        report = _report(
            {
                "harness/skill_evaluation.py": (
                    "from ..knowledge_operations import KnowledgeReader\n"
                ),
                "harness/__init__.py": "from . import skill_evaluation\n",
                "harness/runner.py": "VALUE = 1\n",
                "harness/api.py": "VALUE = 1\n",
            }
        )
        for module in ("runner", "api"):
            self.assertIn(
                f"inferrsi.harness.{module} reaches inferrsi.knowledge_operations: "
                f"inferrsi.harness.{module} -> inferrsi.harness -> "
                "inferrsi.harness.skill_evaluation -> inferrsi.knowledge_operations",
                report.violations,
            )

    def test_core_submodule_walk_includes_own_package_init(self) -> None:
        # Knowledge, benchmark, and apps package inits are already starts
        # under the same core rule, so a leak in those inits was already
        # detected from the package. Own-parent edges also attribute the
        # path from a submodule that Python actually loaded.
        cases = (
            (
                {
                    "knowledge/__init__.py": "from inferrsi import catalog\n",
                    "knowledge/catalog.py": "VALUE = 1\n",
                },
                "inferrsi.knowledge.catalog reaches inferrsi.catalog: "
                "inferrsi.knowledge.catalog -> inferrsi.knowledge -> inferrsi.catalog",
            ),
            (
                {
                    "benchmark/__init__.py": "from inferrsi import catalog\n",
                    "benchmark/probe.py": "VALUE = 1\n",
                },
                "inferrsi.benchmark.probe reaches inferrsi.catalog: "
                "inferrsi.benchmark.probe -> inferrsi.benchmark -> inferrsi.catalog",
            ),
            (
                {
                    "apps/__init__.py": "from inferrsi import catalog\n",
                    "apps/main.py": "VALUE = 1\n",
                },
                "inferrsi.apps.main reaches inferrsi.catalog: "
                "inferrsi.apps.main -> inferrsi.apps -> inferrsi.catalog",
            ),
        )
        for changes, expected in cases:
            with self.subTest(expected=expected):
                report = _report(changes)
                self.assertIn(expected, report.violations, report.violations)

    def test_returning_skill_evaluation_infra_edge_fails(self) -> None:
        # #410 counterexample: S01 removed skill_evaluation -> infra.artifacts.
        # Adding it back is reported hit by hit and keeps isolation false.
        tree = {
            "infra/__init__.py": "",
            "infra/artifacts.py": "VALUE = 1\n",
            "harness/skill_evaluation.py": "from ..infra.artifacts import VALUE\n",
            "harness/improvement.py": "from . import skill_evaluation\n",
        }
        cleared = _report({}, manifest=EMPTY_ALLOWLIST, base=CLEARED_TREE)
        self.assertEqual(cleared.harness_model_infra, ())
        self.assertTrue(cleared.isolation_complete)
        report = _report(tree, manifest=EMPTY_ALLOWLIST, base=CLEARED_TREE)
        self.assertEqual(
            report.harness_model_infra,
            (
                "inferrsi.harness.improvement -> inferrsi.harness.skill_evaluation"
                " -> inferrsi.infra",
                "inferrsi.harness.improvement -> inferrsi.harness.skill_evaluation"
                " -> inferrsi.infra.artifacts",
                "inferrsi.harness.skill_evaluation -> inferrsi.infra",
                "inferrsi.harness.skill_evaluation -> inferrsi.infra.artifacts",
            ),
        )
        self.assertEqual(
            report.blockers,
            (
                "harness reaches model/infra: inferrsi.harness.improvement -> "
                "inferrsi.harness.skill_evaluation -> inferrsi.infra (4 hits)",
            ),
        )
        self.assertFalse(report.isolation_complete)

    def test_mixed_harness_tree_is_not_an_isolated_core(self) -> None:
        tree = {"__init__.py": "", "harness/__init__.py": "", "harness/runner.py": "VALUE = 1\n"}
        report = _report({}, base=tree)
        self.assertEqual(report.violations, ())
        self.assertFalse(report.new_core_closure_isolated)
        self.assertFalse(report.isolation_complete)


class StagedIsolationTest(unittest.TestCase):
    """A01-4: any one trigger keeps overall isolation false."""

    def test_overall_true_only_when_every_trigger_is_cleared(self) -> None:
        report = _report({}, manifest=EMPTY_ALLOWLIST, base=CLEARED_TREE)
        self.assertEqual(report.blockers, ())
        self.assertTrue(report.new_core_closure_isolated)
        self.assertTrue(report.isolation_complete)

    def test_each_trigger_alone_keeps_overall_false(self) -> None:
        triggers = (
            ("absent: knowledge/", {"knowledge/__init__.py": None, "knowledge/catalog.py": None},
             EMPTY_ALLOWLIST),
            ("absent: benchmark/", {"benchmark/__init__.py": None}, EMPTY_ALLOWLIST),
            ("absent: apps/", {"apps/__init__.py": None, "apps/main.py": None}, EMPTY_ALLOWLIST),
            ("allowlist: 9 compatibility pairs", {}, MANIFEST),
            (
                "harness reaches model/infra: inferrsi.harness.skill_evaluation"
                " -> inferrsi.infra",
                {
                    "infra/__init__.py": "",
                    "infra/artifacts.py": "VALUE = 1\n",
                    "harness/skill_evaluation.py": "from ..infra.artifacts import VALUE\n",
                },
                EMPTY_ALLOWLIST,
            ),
            (
                "harness reaches model/infra: inferrsi.harness.runner -> inferrsi.infra",
                {
                    "infra/__init__.py": "",
                    "infra/local.py": "",
                    "harness/runner.py": "from inferrsi.infra import local\n",
                },
                EMPTY_ALLOWLIST,
            ),
            (
                "harness reaches model/infra: inferrsi.harness.runner -> inferrsi.execution"
                " -> inferrsi.model",
                {
                    "execution.py": "from inferrsi.model import metrics\n",
                    "model/__init__.py": "",
                    "model/metrics.py": "",
                    "harness/runner.py": "from inferrsi import execution\n",
                },
                EMPTY_ALLOWLIST,
            ),
        )
        for expected, changes, manifest in triggers:
            with self.subTest(expected=expected):
                report = _report(changes, manifest=manifest, base=CLEARED_TREE)
                self.assertEqual(report.violations, ())
                self.assertEqual(len(report.blockers), 1, report.blockers)
                self.assertTrue(report.blockers[0].startswith(expected), report.blockers)
                self.assertFalse(report.isolation_complete)

    def test_empty_package_directories_are_unscannable(self) -> None:
        # Empty knowledge/, benchmark/, and apps/ directories must not make
        # isolation_complete true; an empty directory is not a scannable package.
        files = {
            relative: source
            for relative, source in CLEARED_TREE.items()
            if not relative.startswith(("knowledge/", "benchmark/", "apps/"))
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative, source in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            for name in ("knowledge", "benchmark", "apps"):
                (root / name).mkdir()
            report = first_wave_report(root, EMPTY_ALLOWLIST, SYNTHETIC_H_ENTRIES)
        self.assertFalse(report.isolation_complete)
        for name in ("knowledge", "benchmark", "apps"):
            self.assertIn(f"unscannable: {name}/", report.blockers)
            self.assertNotIn(f"absent: {name}/", report.blockers)

    def test_clean_new_core_graph_never_sets_overall_true(self) -> None:
        report = _report({})
        self.assertEqual(report.violations, ())
        self.assertTrue(report.new_core_closure_isolated)
        self.assertEqual(blocker_kinds(report), {"allowlist"})
        self.assertFalse(report.isolation_complete)


if __name__ == "__main__":
    unittest.main()
