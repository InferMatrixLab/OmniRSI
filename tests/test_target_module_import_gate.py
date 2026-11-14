"""Guard new Knowledge / Harness / Benchmark / Apps import edges.

A passing scan is not module isolation. The target packages are not all
present, and the existing ``inferrsi.harness`` tree still mixes other layers.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from tests import _gate_policy_support as support
from tests._gate_policy_support import (
    NEW_CORES,
    ROOT,
    blocker_kinds,
    cross_core_violations,
    exact_pairs,
    first_wave_report,
    load_manifest,
)


@pytest.mark.parametrize("extra", [
    "\nimport importlib\nimportlib.import_module('inferrsi.knowledge')\n",
    "\nimportlib.util.module_from_spec(spec)\n",
    "\ndef another():\n    return importlib.util.module_from_spec(spec)\n",
])
def test_declared_resource_loader_does_not_allow_an_extra_dynamic_edge(tmp_path, extra):
    task = tmp_path / "harness/task_protocol.py"
    task.parent.mkdir()
    source = ("import importlib.util\ndef protocol():\n"
              "    return importlib.util.module_from_spec(spec)\n")
    task.write_text(source, encoding="utf-8")
    report = first_wave_report(tmp_path)
    assert not report.violations
    assert "runtime resource loaders" in blocker_kinds(report)
    assert not report.isolation_complete
    task.write_text(source + extra, encoding="utf-8")
    assert any("dynamic import" in item for item in first_wave_report(tmp_path).violations)


def test_resource_loader_declaration_does_not_cover_an_adjacent_module(tmp_path):
    task = tmp_path / "harness/other_protocol.py"
    task.parent.mkdir()
    task.write_text(
        "import importlib.util\ndef protocol():\n"
        "    return importlib.util.module_from_spec(spec)\n",
        encoding="utf-8",
    )
    assert any("dynamic import" in item for item in first_wave_report(tmp_path).violations)


class TargetModuleImportGateTest(unittest.TestCase):
    def test_no_new_cross_core_imports(self) -> None:
        self.assertEqual(cross_core_violations(allowed=exact_pairs(load_manifest())), [])

    def test_first_wave_policy_holds_on_current_tree(self) -> None:
        self.assertEqual(first_wave_report().violations, ())

    def test_clean_scan_is_not_isolation(self) -> None:
        # Facts that hold at every stage.
        self.assertTrue((ROOT / "benchmark" / "registry.py").is_file())
        self.assertTrue((ROOT / "harness").is_dir())
        self.assertTrue((ROOT / "contracts.py").is_file())
        self.assertFalse((ROOT / "contracts").is_dir())
        # A01-4 staged model: packages may appear one at a time, but each
        # remaining trigger keeps overall false.
        report = first_wave_report()
        self.assertFalse(report.isolation_complete)
        for name in NEW_CORES:
            absent = not (ROOT / name).is_dir()
            self.assertEqual(f"absent: {name}/" in report.blockers, absent)
            self.assertNotIn(f"unscannable: {name}/", report.blockers)
        kinds = blocker_kinds(report)
        self.assertIn("allowlist", kinds)
        # No residual: the gate registers no exempt edge, and the scan above
        # already fails any harness module that reaches an old root leaf.
        self.assertFalse(hasattr(support, "REGISTERED_RESIDUALS"))
        self.assertFalse(hasattr(report, "residuals"))
        self.assertNotIn("residual", kinds)
        self.assertEqual(report.violations, ())
        self.assertIn("harness reaches model/infra", kinds)

    def test_split_skill_eval_modules_reach_no_model_or_infra(self) -> None:
        # S01: the split Harness modules own their writes and digests. The
        # remaining harness -> model/infra hits belong to other modules.
        report = first_wave_report()
        for name in (
            "skill_evaluation",
            "improvement",
            "artifact_sandbox",
            "atomic_json",
        ):
            with self.subTest(module=name):
                self.assertTrue((ROOT / "harness" / f"{name}.py").is_file())
                self.assertEqual(
                    [
                        path
                        for path in report.harness_model_infra
                        if path.startswith(f"inferrsi.harness.{name} -> ")
                    ],
                    [],
                )
        for name in ("skill_eval_diagnostic", "skill_eval_engineering"):
            self.assertFalse((ROOT / "harness" / f"{name}.py").exists())

    def test_real_tree_allows_apps_and_same_core_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            samples = {
                "harness/probe.py": "from inferrsi import knowledge\n",
                "apps/probe.py": "import inferrsi.knowledge\n",
                "knowledge/bar.py": "VALUE = 1\n",
                "knowledge/foo.py": "from .bar import VALUE\n",
                "knowledge/out.py": "import inferrsi.harness\n",
                "benchmark/edge.py": "from inferrsi.benchmark import edge\n",
                "benchmark/forbidden.py": "import inferrsi.harness\n",
                "cli.py": "from inferrsi.benchmark.registry import builtin_tasks\n",
                "benchmark_operations.py": "import inferrsi.benchmark\n",
                "unowned.py": "import inferrsi.benchmark\n",
            }
            for relative, source in samples.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            violations = cross_core_violations(root)
        self.assertEqual(
            violations,
            [
                "benchmark/forbidden.py imports inferrsi.harness",
                "harness/probe.py imports inferrsi.knowledge",
                "knowledge/out.py imports inferrsi.harness",
                "unowned.py imports inferrsi.benchmark",
            ],
        )

    def test_layout_doc_states_the_same_limit(self) -> None:
        text = (
            Path(__file__).resolve().parents[1] / "docs" / "module-layout.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Isolation stays false", text)
        self.assertIn("Same-core imports are allowed", text)


if __name__ == "__main__":
    unittest.main()
