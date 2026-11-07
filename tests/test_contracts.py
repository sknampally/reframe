"""YAML contract gates. Required fields come from the templates, not a second schema."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_modernize as fixtures
import kiro_workflow as kw
import modernize as app
import modernize_contracts as contracts
import pilot


def complete(kind):
    record = {}
    for path, field in contracts.requirements(kind):
        value = ['fixture-link'] if field in {'list', 'evidence'} else 'fixture'
        cursor = record
        parts = path.split('.')
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = value
    return record


class ContractTests(unittest.TestCase):
    def setUp(self):
        fixtures.PreparationTests.setUp(self)

    def args(self, *values):
        return fixtures.PreparationTests.args(self, *values)

    def assess(self, label='a'):
        return fixtures.PreparationTests.assess(self, label)

    def approve(self, phase, *extra):
        return fixtures.PreparationTests.approve(self, phase, *extra)

    def baseline(self):
        return fixtures.PreparationTests.baseline(self)

    def test_yaml_markers_are_the_required_fields(self):
        for kind, filename in contracts.CONTRACTS.items():
            marked = []
            for line in (app.FRAMEWORK / 'contracts' / filename).read_text().splitlines():
                body, _, comment = line.partition('#')
                if 'REQUIRED' in body or 'REQUIRED' in comment:
                    marked.append(body.split(':', 1)[0].strip())
            leaves = [path.split('.')[-1] for path, _ in contracts.requirements(kind)]
            self.assertEqual(sorted(marked), sorted(leaves))
            self.assertNotIn('contract_version', leaves)

    def test_missing_task_field_stops_before_aws(self):
        record = complete('task')
        record.pop('task_id')
        (self.work / 'task-contract.json').write_text(json.dumps(record))
        with patch.object(kw, 'initialize', side_effect=AssertionError('aws')), \
             patch.object(kw, 'assessment_step', side_effect=AssertionError('aws')):
            with self.assertRaisesRegex(pilot.Blocked, 'Task contract missing task_id'):
                self.assess()

    def test_complete_task_contract_allows_assessment(self):
        (self.work / 'task-contract.json').write_text(json.dumps(complete('task')))
        result = self.assess()
        self.assertTrue(result['status'])
        self.assertFalse(list(Path(result['run']).glob('*.zip')))

    def _ready_for_reviewer(self):
        self.baseline()
        area = self.work / 'preparation'
        area.mkdir()
        app.write(area / 'candidate-1.json', {'kind': 'candidate'})
        self.project['pipeline'] = {
            'round': 1, 'role': 'reviewer', 'calls': 0, 'contexts': {}, 'reads': {},
            'status': 'RUNNING', 'candidate_sha256': app.sha(area / 'candidate-1.json')}
        app.persist(self.work, self.project)

    def test_missing_review_evidence_stops_before_the_model(self):
        self._ready_for_reviewer()
        record = complete('review')
        record['checks_performed'] = []
        (self.work / 'review-record.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(pilot.Blocked, 'Review contract missing evidence link checks_performed'):
            app.legacy_pipeline(self.work, self.project, self.args('validate'),
                                runtime=lambda *args: self.fail('model call'))
        self.assertEqual(self.project['pipeline']['calls'], 0)

    def test_complete_review_contract_reaches_the_model(self):
        self._ready_for_reviewer()
        (self.work / 'review-record.json').write_text(json.dumps(complete('review')))
        def runtime(*args):
            raise pilot.Blocked('model-was-called')
        with self.assertRaisesRegex(pilot.Blocked, 'model-was-called'):
            app.legacy_pipeline(self.work, self.project, self.args('validate'), runtime=runtime)

    def test_missing_approval_evidence_stops_the_stage(self):
        self.assess()
        record = complete('approval')
        record.pop('review_record_revision')
        (self.work / 'approval-record.json').write_text(json.dumps(record))
        before = json.loads(json.dumps(self.project['approvals']))
        with self.assertRaisesRegex(pilot.Blocked, 'Approval contract missing evidence link review_record_revision'):
            self.approve('assessment', '--label', 'a', '--confirm-source-match')
        self.assertEqual(self.project['approvals'], before)

    def test_complete_approval_contract_allows_the_stage(self):
        self.assess()
        (self.work / 'approval-record.json').write_text(json.dumps(complete('approval')))
        result = self.approve('assessment', '--label', 'a', '--confirm-source-match')
        self.assertEqual(result['status'], 'ASSESSMENT_APPROVED')


if __name__ == '__main__':
    unittest.main()
