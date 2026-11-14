"""Offline PR mapping plumbing; no provider or maintained mapping changes."""
import tempfile
import unittest
from pathlib import Path

from inferrsi.benchmark import skill_eval_diagnostic as diagnostic
from inferrsi.infra.artifacts import write_json
from tests.fixtures.skill_eval_compare_support import FixedOutputClient
from tests.fixtures.skill_eval_pr_support import setup
from tests.test_skill_eval_diagnostic import CASES, answer
from tools import skill_eval_compare as compare
from tools import skill_eval_pr as integration


class PRDiagnosticTests(unittest.TestCase):
    def prepare(self, root, mode='update', adapter=diagnostic.ADAPTER):
        repo, path, identity = setup(root, mode, ids=('one',))
        mapping = compare.read_json(path)
        mapping['skills'][0]['adapter'] = adapter
        write_json(path, mapping)
        write_json(root / 'cases.json', CASES[:1])
        output = root / 'planned'
        plan = integration.prepare(repo, path, identity, output)
        return plan, output, identity

    def test_explicit_diagnostic_mapping_preserves_exact_pair_and_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, output, identity = self.prepare(Path(directory))
            self.assertEqual(plan['estimated_calls'], 2)
            frozen = compare.load_plan(output / 'selected')
            self.assertEqual(frozen['adapter'], diagnostic.ADAPTER)
            self.assertEqual(frozen['mode'], 'update')
            for role, revision in [('baseline', identity['base']), ('candidate', identity['head'])]:
                self.assertEqual(frozen['arms'][role]['git_revision'], revision)
                self.assertIsNotNone(frozen['arms'][role]['bundle'])
            self.assertEqual([e for e in plan['coverage'] if e['coverage'] != 'covered'],
                             [{'path': 'exam.txt', 'status': 'M', 'coverage': 'uncovered'}])
            self.assertEqual(list((output / 'selected/results').iterdir()), [])

    def test_pr_report_rechecks_diagnostic_not_audit_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, output, _ = self.prepare(root)
            client = FixedOutputClient(root)
            write_json(client.answer, answer(0))
            item = plan['comparisons'][0]
            compare.execute(output / 'selected', item['plan_sha256'], client=client)
            result = integration.reports(output)[0]
            self.assertEqual(result['pairs'][0]['outcome'], 'unchanged_correct')

    def test_unknown_adapter_cannot_fall_back_to_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, _, _ = self.prepare(Path(directory), adapter='untrusted-kind')
            self.assertEqual(plan['estimated_calls'], 0)
            self.assertIn('Unknown experiment adapter', plan['comparisons'][0]['unsupported'])
            self.assertTrue(any(e['coverage'] == 'unsupported' for e in plan['coverage']))

    def test_addition_cannot_claim_incremental_old_new_diagnosis(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, _, _ = self.prepare(Path(directory), mode='addition')
            self.assertEqual(plan['estimated_calls'], 0)
            self.assertIn('paired old/new bundles', plan['comparisons'][0]['unsupported'])
            self.assertTrue(any(e['coverage'] == 'unsupported' for e in plan['coverage']))


if __name__ == '__main__':
    unittest.main()
