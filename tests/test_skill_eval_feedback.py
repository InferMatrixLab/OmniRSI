"""Offline public projection checks: no model/provider calls or grader changes."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from inferrsi.benchmark import skill_eval_diagnostic as diagnostic
from inferrsi.infra.artifacts import sha256, write_json
from tests.fixtures.skill_eval_compare_support import FixedOutputClient
from tests.fixtures.skill_eval_pr_support import approval, setup
from tools import skill_eval_feedback as feedback
from tools import skill_eval_pr as integration

ROOT = Path(__file__).resolve().parents[1]
TASK_PATH = ROOT / 'tests/fixtures/skill-comparison/pr133-diagnostic-case05.json'
TASK = json.loads(TASK_PATH.read_text())[0]
OLD = dict(diagnosis='capacity', evidence=['E4'], next_check='inspect_allocator_accounting',
           reuse_authorized=False)
NEW = dict(diagnosis='readiness', evidence=['E2', 'E4'], next_check='check_receive_completion',
           reuse_authorized=False)


def trial(answer):
    checks = diagnostic.verify(TASK, answer)
    return dict(verdict='PASS' if checks['passed'] else 'FAIL', grader_trust='reverified',
                feedback=feedback.projection(diagnostic.ADAPTER, TASK, answer, checks))


def fixture():
    exam = dict(cases='f' * 64, schema='a' * 64, grader='9' * 64)
    plan = dict(repository='example/fixture', pr=7, base='b' * 40, head='c' * 40,
                version='d' * 40, model=dict(name='offline', effort='test'),
                limits=dict(repeats=1, max_task_calls=2, timeout_seconds=60,
                            campaign_seconds=600, token_stop_new_trials=1000),
                estimated_calls=2, coverage=[], comparisons=[dict(
                    skill='selected', mode='update', calls=2, plan_sha256='e' * 64,
                    cases=[dict(id='case-05', split='holdout')], exam=exam)])
    result = dict(skill='selected', plan_sha256='e' * 64, exam=exam,
                  arms={r: dict(git_revision=plan[k]) for r, k in
                        [('baseline', 'base'), ('candidate', 'head')]},
                  status=dict(execution_state='completed'), discovery=[],
                  pairs=[dict(case_id='case-05', repeat=1, outcome='unchanged_incorrect',
                              baseline=trial(OLD), candidate=trial(NEW))],
                  counts={k: int(k == 'unchanged_incorrect') for k in
                          ('fix', 'regression', 'unchanged_correct', 'unchanged_incorrect',
                           'inconclusive', 'missing_pair')})
    return plan, [result]


def render(plan, results, **kwargs):
    return integration.summary(plan, results, 'https://example.invalid/evidence', 100, 1, **kwargs)


class FeedbackTests(unittest.TestCase):
    def test_retained_case_changes_components_not_total(self):
        plan, results = fixture()
        text = render(plan, results)
        self.assertIn('unchanged_incorrect', text)
        self.assertEqual(text.count('diagnosis=FAIL'), 1)
        self.assertEqual(text.count('diagnosis=PASS'), 1)
        self.assertEqual(text.count('citation=FAIL'), 2)
        self.assertEqual(text.count('reuse_authorized=false'), 2)
        self.assertIn('Extra/nondecisive citation', text)
        self.assertIn('capacity', text)
        self.assertIn('readiness', text)
        self.assertNotIn('expected', text.lower())
        self.assertLess(text.index('Retained baseline'), text.index('### Advisory'))

    def test_component_regression_with_unchanged_strict_failure(self):
        plan, results = fixture()
        pair = results[0]['pairs'][0]
        pair['baseline'], pair['candidate'] = pair['candidate'], pair['baseline']
        text = render(plan, results)
        self.assertLess(text.index('diagnosis=PASS'), text.index('diagnosis=FAIL'))
        self.assertIn('unchanged_incorrect', text)

    def test_execution_block_does_not_obscure_evidence(self):
        plan, results = fixture()
        plan['execution_blocked'] = 'accounting unavailable'
        text = render(plan, results)
        self.assertLess(text.index('readiness'), text.index('Paid execution blocked'))
        self.assertIn('does not invalidate available retained results', text)

    def test_bounded_block_reason_keeps_explicit_no_fallback_safety_clause(self):
        plan, results = fixture()
        plan["execution_blocked"] = "</details>@someone `unsafe` " * 1000
        text = render(plan, results)
        self.assertIn("no alternate provider/model is selected", text)
        self.assertIn("does not invalidate available retained results", text)
        self.assertEqual(text.count("</details>"), 2)
        self.assertNotIn("@someone", text)
        self.assertNotIn("`unsafe`", text)
        self.assertLess(len(text), 10000)

    def test_unavailable_and_schema_failure_are_not_component_failures(self):
        for t in ({}, dict(verdict='INCONCLUSIVE', grader_trust='reverified'),
                  dict(verdict='FAIL', grader_trust='unverified')):
            self.assertIn('unavailable', feedback.trial_text(t))
            self.assertNotIn('diagnosis=FAIL', feedback.trial_text(t))
        text = feedback.trial_text(trial({'reason': 'secret-canary'}))
        self.assertIn('schema=FAIL', text)
        self.assertIn('diagnosis=unavailable', text)
        self.assertNotIn('secret-canary', text)

    def test_citation_reason_classification(self):
        for ids, present, absent in [(['E2', 'E4'], 'extra_citation', 'missing_citation'),
                                      ([], 'missing_citation', 'extra_citation'),
                                      (['E2', 'E2'], 'duplicate_citation', 'extra_citation')]:
            data = trial({**NEW, 'evidence': ids})['feedback']
            self.assertIn(present, data['reasons'])
            self.assertNotIn(absent, data['reasons'])

    def test_oversized_and_arbitrary_fields_are_not_published(self):
        for ids in [['E' + '1' * 10000], ['E2'] * 10000, ['secret-canary']]:
            text = feedback.trial_text(trial({**NEW, 'evidence': ids}))
            self.assertNotIn('secret-canary', text)
            self.assertLess(len(text), 1500)
        for field, value in [('diagnosis', '<details>@secret-canary'),
                             ('reuse_authorized', 1), ('next_check', {'secret-canary': 1})]:
            text = feedback.trial_text(trial({**NEW, field: value}))
            self.assertNotIn('secret-canary', text)
        t = trial(NEW)
        t['feedback']['components']['citation'] = 1
        self.assertIn('invalid projection', feedback.trial_text(t))

    def test_mismatched_and_unavailable_evidence_suppresses_details_and_counts(self):
        for mutation in ('head', 'base', 'exam', 'plan_sha256', 'duplicate'):
            plan, results = fixture()
            if mutation in ('head', 'base'):
                plan[mutation] = 'f' * 40
            elif mutation == 'duplicate':
                results.append(copy.deepcopy(results[0]))
            else:
                results[0][mutation] = 'wrong'
            text = render(plan, results)
            self.assertNotIn('readiness', text)
            self.assertNotIn('"unchanged_incorrect":1', text)
        for state in ('expired', 'missing', 'invalid'):
            plan, results = fixture()
            text = render(plan, results, evidence_state=state)
            self.assertNotIn('readiness', text)
            self.assertNotIn('"unchanged_incorrect":1', text)

    def test_projection_is_bounded_and_never_echoes_freeform_or_oracle(self):
        t = trial(NEW)
        t['feedback']['answer']['reason'] = 'secret-canary'
        t['feedback']['oracle'] = 'hidden-secret-canary'
        self.assertNotIn('secret-canary', feedback.trial_text(t))
        plan, results = fixture()
        results[0]['pairs'][0]['case_id'] = '<details>@secret-canary'
        self.assertNotIn('secret-canary', '\n'.join(feedback.comparison_lines(
            plan, results, 'available')))
        self.assertEqual(feedback.code('@secret-canary'), 'unavailable')

    def test_actual_reports_regrade_and_replace_untrusted_projection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, mapping, identity = setup(root, mode="addition", ids=("one",))
            output = root / "evaluation"
            plan = integration.prepare(repo, mapping, identity, output)
            integration.execute(output, approval(plan), client=FixedOutputClient(root))
            result = integration.reports(output)[0]
            self.assertEqual(result["pairs"][0]["candidate"]["feedback"]["adapter"], "audit")
            text = render(plan, [result])
            self.assertIn("report_verdict=PASS", text)
            path = output / "selected/results/one-candidate-1/result.json"
            record = json.loads(path.read_text())
            record["feedback"] = {"answer": "secret-canary", "components": {"injected": True}}
            write_json(path, record)
            write_json(path.with_name("receipt.json"), {"result_sha256": sha256(path)})
            text = render(plan, integration.reports(output))
            self.assertNotIn("secret-canary", text)
            self.assertNotIn("injected", text)
            record["checks"]["assertions"]["report_verdict"] = False
            write_json(path, record)
            write_json(path.with_name("receipt.json"), {"result_sha256": sha256(path)})
            with self.assertRaisesRegex(ValueError, "Untrusted grading"):
                integration.reports(output)

    def test_missing_pair_and_consistently_hostile_metadata(self):
        plan, results = fixture()
        results[0]["pairs"][0]["candidate"] = {"verdict": "INCONCLUSIVE", "missing": True}
        text = render(plan, results)
        self.assertIn("Candidate: unavailable", text)
        self.assertNotIn("Candidate: FAIL", text)
        bad = "</details>\n@someone `evil` | # forged"
        plan["comparisons"][0]["skill"] = results[0]["skill"] = bad
        plan["comparisons"][0]["cases"][0]["id"] = bad
        results[0]["pairs"][0]["case_id"] = bad
        text = render(plan, results)
        self.assertEqual(text.count("</details>"), 2)
        self.assertNotIn("@someone", text)
        self.assertNotIn("`evil`", text)
        self.assertNotIn("\n# forged", text)

    def test_changed_grader_cannot_generate_current_explanations(self):
        result = {"pairs": [{"baseline": trial(OLD), "candidate": trial(NEW)}],
                  "discovery": []}
        context = dict(adapter=diagnostic.ADAPTER, schema=diagnostic.SCHEMA,
                       exam={"grader": "f" * 64}, tasks={TASK["id"]: TASK})
        integration.attach_feedback(result, context)
        self.assertNotIn("feedback", result["pairs"][0]["baseline"])

    def test_historical_publish_preserves_original_plan_and_new_writer(self):
        plan, results = fixture()
        context = dict(adapter=diagnostic.ADAPTER, schema=diagnostic.SCHEMA,
                       exam={"grader": "f" * 64}, tasks={TASK["id"]: TASK})
        retained = dict(plan=plan, reports=results, contexts={"selected": context},
                        expires_at=100, evidence_url="https://example.invalid/evidence")
        api = Mock()
        api.publish.return_value = {"html_url": "https://example.invalid/comment"}
        with tempfile.TemporaryDirectory() as temporary, \
                patch("tools.skill_eval_retained.retained_reports", return_value=retained), \
                patch.object(integration, "git", return_value=b"a" * 40):
            output = Path(temporary)
            integration.publish_historical(api, 7, 10, 20, now=1, output=output)
            self.assertEqual(json.loads((output / "pr-plan.json").read_text()), plan)
            self.assertEqual(json.loads((output / "report-renderer.json").read_text())[
                "original_plan_sha256"], integration.fingerprint(plan))
            self.assertTrue((output / "selected/report.json").is_file())
        args = api.publish.call_args.args
        self.assertIs(args[0], plan)
        self.assertEqual(args[2], 20)
        self.assertIn("Presentation implementation: `" + "a" * 40, args[1])
        self.assertIn("starts no model execution or budget reservation", args[1])

    def test_copyable_json_and_metadata_escape_are_not_double_escaped(self):
        value = [{"outcome": "unchanged_incorrect", "case": "[one]```\n</details>@someone"}]
        text = feedback.json_for_code(value)
        self.assertEqual(json.loads(text), value)
        self.assertIn("unchanged_incorrect", text)
        self.assertNotIn("```", text)
        self.assertNotIn("</details>", text)
        self.assertNotIn("@someone", text)
        self.assertEqual(feedback.safe_text("@someone_[x]"),
                         "&#64;someone&#95;&#91;x&#93;")

    def test_historical_validation_failure_never_overwrites_comment(self):
        api = Mock()
        with patch("tools.skill_eval_retained.retained_reports", side_effect=ValueError("stale")), \
                patch.object(integration, "git", return_value=b"a" * 40):
            with self.assertRaisesRegex(ValueError, "stale"):
                integration.publish_historical(api, 7, 10, 20, now=1)
        api.publish.assert_not_called()


if __name__ == '__main__':
    unittest.main()
