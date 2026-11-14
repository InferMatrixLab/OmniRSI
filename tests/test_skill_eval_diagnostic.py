import copy
import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from inferrsi.benchmark import skill_eval_diagnostic as diagnostic
from inferrsi.infra.artifacts import sha256, write_json
from tests.fixtures.skill_eval_compare_support import FixedOutputClient, configuration, git
from tools import skill_eval_compare as compare

CASES = json.loads(
    (Path(__file__).parent / 'fixtures/skill_eval_diagnostic_cases.json').read_text())
# Independently authored expected responses: never emitted into model workspaces.
ANSWERS = [
    ('readiness', ['E2'], 'check_receive_completion'),
    ('capacity', ['E2', 'E3', 'E4'], 'inspect_allocator_accounting'),
    ('invalid_reuse', ['E2'], 'check_transfer_integrity'),
    ('insufficient_evidence', ['E1'], 'collect_missing_events'),
    ('readiness', ['E2'], 'check_receive_completion'),
    ('capacity', ['E2', 'E3', 'E4'], 'inspect_allocator_accounting'),
    ('invalid_reuse', ['E3'], 'check_transfer_integrity'),
    ('insufficient_evidence', ['E2', 'E3'], 'collect_missing_events'),
    ('insufficient_evidence', ['E2', 'E3'], 'collect_missing_events'),
    ('invalid_reuse', ['E2'], 'check_transfer_integrity'),
]


def answer(index):
    diagnosis, evidence, check = ANSWERS[index]
    return dict(diagnosis=diagnosis, evidence=evidence.copy(), next_check=check,
                reuse_authorized=False)


class DiagnosticTests(unittest.TestCase):
    def test_counterfactual_cases(self):
        for i, task in enumerate(CASES):
            with self.subTest(task=task['id']):
                self.assertEqual(diagnostic.expected(task)[0], ANSWERS[i][0])
                self.assertTrue(diagnostic.verify(task, answer(i))['passed'])
                self.assertEqual(set(task), {'id', 'split', 'request', 'provenance', 'events'})
                self.assertNotIn('expected', json.dumps(task))

    def test_exhaustive_state_table(self):
        kinds = tuple(diagnostic.VALUES)
        count = 0
        for values in itertools.product(*(tuple(diagnostic.VALUES[k]) + (None,) for k in kinds)):
            states = dict(zip(kinds, values))
            events = [dict(id=f'E{i}', kind=k, value=v)
                      for i, (k, v) in enumerate(states.items()) if v is not None]
            if not events:
                continue
            count += 1
            actual = diagnostic.expected({**CASES[0], 'events': events})[0]
            if states['identity'] == 'mismatch' or states['receive'] == 'failed':
                self.assertEqual(actual, 'invalid_reuse')
            elif states['receive'] == 'pending':
                self.assertEqual(actual, 'readiness')
            elif all(states[k] == v for k, v in
                     [('receive', 'complete'), ('identity', 'match'), ('allocator', 'denied')]):
                self.assertEqual(actual, 'capacity')
            else:
                self.assertEqual(actual, 'insufficient_evidence')
        self.assertEqual(count, 143)

    def test_reject_wrong_or_unsafe_answer(self):
        for field, value in [
            ('evidence', ['E999']), ('evidence', []), ('evidence', ['E2', 'E2']),
            ('evidence', ['E1', 'E2', 'E3']),
            ('diagnosis', 'capacity'), ('next_check', 'inspect_allocator_accounting'),
            ('reuse_authorized', True), ('reuse_authorized', 0), ('diagnosis', []),
        ]:
            with self.subTest(field=field, value=value):
                self.assertFalse(diagnostic.verify(CASES[0], {**answer(0), field: value})['passed'])

    def test_insufficient_evidence_accepts_concise_relevant_citations(self):
        result = dict(diagnosis='insufficient_evidence', evidence=['E2', 'E3'],
                      next_check='collect_missing_events', reuse_authorized=False)
        self.assertTrue(diagnostic.verify(CASES[7], result)['passed'])
        self.assertFalse(diagnostic.verify(CASES[7], {**result, 'evidence': ['E3']})['passed'])

    def test_strict_input_boundary(self):
        for mutation in ('answer_key', 'duplicate_id', 'duplicate_kind', 'unknown'):
            task = copy.deepcopy(CASES[0])
            if mutation == 'answer_key':
                task['expected_diagnosis'] = 'readiness'
            elif mutation == 'duplicate_id':
                task['events'][1]['id'] = task['events'][0]['id']
            elif mutation == 'duplicate_kind':
                task['events'].append({**task['events'][0], 'id': 'E99'})
            else:
                task['events'][0]['value'] = 'unknown'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                diagnostic.validate_task(task)

    def test_prepare_and_adapter_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, repo = configuration(root, ids=('one',), max_task_calls=20)
            config = json.loads(path.read_text())
            config['adapter'] = diagnostic.ADAPTER
            write_json(path, config)
            write_json(root / 'cases.json', CASES)
            out = root / 'output'
            compare.prepare(path, repo, out)
            plan = json.loads((out / 'plan/freeze.json').read_text())
            self.assertEqual(compare.load_plan(out), plan)
            self.assertEqual(plan['adapter'], diagnostic.ADAPTER)
            self.assertEqual(plan['prompt'], diagnostic.PROMPT)
            self.assertEqual(json.loads((out / 'plan/schema.json').read_text()), diagnostic.SCHEMA)
            self.assertEqual(plan['exam']['grader'], sha256(Path(diagnostic.__file__)))
            self.assertTrue(all(plan['arms'][role]['bundle'] for role in compare.ROLES))
            self.assertTrue(plan['bundle_changes'])
            self.assertEqual(len(plan['schedule']), 20)
            self.assertEqual(json.loads((out / 'plan/tasks.json').read_text()), CASES)
            for change in [{'mode': 'addition'}, {'grader': 'x'}, {'schema': 'x'},
                           {'adapter': 'unknown'}]:
                with self.subTest(change=change), self.assertRaises(ValueError):
                    compare.validate({**config, **change}, CASES)

    def test_fake_client_paired_trial_and_report(self):
        # This is plumbing coverage using prewritten responses, not a model experiment.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, repo = configuration(root, ids=('one',))
            config = json.loads(path.read_text())
            config['adapter'] = diagnostic.ADAPTER
            write_json(path, config)
            write_json(root / 'cases.json', CASES[:1])
            out = root / 'output'
            authorization = compare.prepare(path, repo, out)
            client = FixedOutputClient(root)
            write_json(client.answer, answer(0))
            compare.execute(out, authorization, client=client)
            report = compare.read_report(out)
            self.assertEqual(report['pairs'][0]['outcome'], 'unchanged_correct')
            for role in compare.ROLES:
                self.assertEqual(report['pairs'][0][role]['grader_trust'], 'reverified')
            self.assertEqual(len(client.calls), 2)
            for call in client.calls:
                self.assertTrue(all('verify.py' not in path for path in call['inputs']))

    def test_selected_closure_keeps_legacy_blanket_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, repo = configuration(root, ids=('one',))
            unrelated = repo / 'library/shared/not-selected.py'
            unrelated.write_text('raise AssertionError("must never execute")\n')
            git(repo, 'add', str(unrelated))
            git(repo, 'update-index', '--chmod=+x', 'library/shared/not-selected.py')
            git(repo, 'commit', '--quiet', '-m', 'Unrelated executable fixture')
            revision = git(repo, 'rev-parse', 'HEAD')
            with self.assertRaisesRegex(ValueError, 'Executable'):
                compare.snapshot(repo, revision, 'library', root / 'blanket')
            config = json.loads(path.read_text())
            config['adapter'] = diagnostic.ADAPTER
            config['snapshots']['candidate']['git_revision'] = revision
            write_json(path, config)
            write_json(root / 'cases.json', CASES[:1])
            out = root / 'output'
            compare.prepare(path, repo, out)
            manifest = json.loads((out / 'plan/snapshots/candidate.json').read_text())
            self.assertNotIn('library/shared/not-selected.py', manifest['entries'])
            self.assertIn('library/shared/notes.md', manifest['entries'])
            self.assertEqual(compare.load_plan(out)['arms']['candidate']['git_revision'], revision)

    def test_legacy_audit_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, repo = configuration(root, ids=('one',))
            config = json.loads(path.read_text())
            with self.assertRaises(ValueError):
                compare.validate(config, CASES[:1], default_grader=False)
            out = root / 'output'
            compare.prepare(path, repo, out)
            plan = compare.load_plan(out)
            self.assertEqual(plan['prompt'], compare.PROMPT)
            self.assertEqual(json.loads((out / 'plan/schema.json').read_text()), compare.SCHEMA)
            self.assertEqual(plan['exam']['grader'], sha256(compare.ROOT /
                                                          'tools/skill_eval_verifier.py'))

    def test_standalone_frozen_verifier(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            verifier = root / 'verify.py'
            verifier.write_bytes(Path(diagnostic.__file__).read_bytes())
            write_json(root / 'task.json', CASES[0])
            write_json(root / 'answer.json', answer(0))
            process = subprocess.run(
                [sys.executable, str(verifier), str(root / 'task.json'),
                 str(root / 'answer.json')], cwd=root, capture_output=True,
                env={'INFERRSI_RESULT_PATH': str(root / 'result.json')},
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertTrue(json.loads((root / 'result.json').read_text())['passed'])

    def test_retained_answer_reverified_not_trusted(self):
        task = CASES[0]
        result = answer(0)
        trial = dict(task_id=task['id'], verdict='PASS', output=json.dumps(result),
                     checks=diagnostic.verify(task, result))
        plan = {'exam': {'grader': sha256(Path(diagnostic.__file__))}}
        compare.verify_retained_grader(plan, [trial], {task['id']: task})
        self.assertEqual(trial['grader_trust'], 'reverified')
        trial['output'] = json.dumps({**result, 'reuse_authorized': True})
        with self.assertRaisesRegex(ValueError, 'disagrees'):
            compare.verify_retained_grader(plan, [trial], {task['id']: task})


if __name__ == '__main__':
    unittest.main()
