import json
import re
import tempfile
import unittest
from pathlib import Path

import reframe_handoff as handoff


class HandoffTests(unittest.TestCase):
    def documents(self):
        rules = [{'id': 'R1', 'program': 'CLAIMINF', 'source_path': 'copybooks/claiminf.cpy'}]
        paths = ['copybooks/claiminf.cpy', 'jcl/nightly.jcl', 'src/main.txt']
        apis = [{'id': 'read', 'method': 'GET', 'path': '/claiminf'}]
        return handoff.documents(rules, paths, apis)

    def test_sequence_capabilities_and_unrun_cases_stay_open(self):
        payload = self.documents()
        sequence = payload['modernization-sequence.json']
        self.assertEqual([step['id'] for step in sequence], [
            'stabilize', 'expose', 'decompose', 'replace-one-slice', 'validate-in-parallel', 'retire-after-signoff'])
        self.assertTrue(all(step['status'] == 'NOT_STARTED' for step in sequence))
        capability_map = payload['capability-map.json']
        self.assertEqual(capability_map['acceptance'], 'PROPOSAL')
        capability = capability_map['capabilities'][0]
        self.assertEqual(capability['disposition'], 'unassigned')
        self.assertEqual(capability['allowed_dispositions'], ['unassigned', 'retain', 'expose', 'rewrite', 'retire'])
        plan = payload['parallel-validation.json']
        self.assertEqual(plan['oracle'], 'Legacy runtime oracle not established')
        self.assertEqual([case['name'] for case in plan['rules'][0]['cases']],
                         ['match', 'mismatch', 'missing_input', 'invalid_input', 'boundary'])
        self.assertTrue(all(case['status'] == 'NOT RUN' for case in plan['rules'][0]['cases']))

    def test_inventories_governance_and_boundary_use_only_named_members(self):
        payload = self.documents()
        self.assertEqual(payload['field-inventory.json']['copybooks'], [{'path': 'copybooks/claiminf.cpy', 'fields': []}])
        self.assertEqual(payload['batch-inventory.json']['jobs'], [{'path': 'jcl/nightly.jcl', 'status': 'NOT_STARTED'}])
        self.assertNotIn('src/main.txt', json.dumps(payload['field-inventory.json']))
        self.assertEqual(payload['api-boundary.json']['status'], 'REVIEW')
        self.assertEqual(payload['api-boundary.json']['violations'][0]['copybook_names'], ['claiminf'])
        governance = payload['governance.json']
        self.assertTrue(governance['synthetic_data_only'])
        self.assertEqual(governance['do_not_rewrite_yet'], ['claiminf'])
        self.assertEqual(governance['retirement_status'], 'NOT_READY')
        self.assertTrue(all(gate['status'] == 'PENDING' for gate in governance['retirement']))
        text = json.dumps(payload).lower()
        for word in ('node', 'react', 'kubernetes', 'terraform', 'formulary', 'blazor'):
            self.assertIsNone(re.search(r'(?<![a-z])' + word + r'(?![a-z])', text))
        self.assertIn('skill-discovery.md', payload)
        self.assertIn('skill-tests.md', payload)
        guide = payload['extraction-reading.json']
        self.assertTrue(guide['source_stays_pinned'])
        self.assertIn('COPY-expanded', guide['line_numbers'])
        self.assertIn('Remove a member from the pinned source', guide['refused_actions'])
        self.assertIn('COPY-expanded', payload['skill-extraction.md'])
        coverage = payload['rule-coverage.json']
        self.assertEqual(coverage['status'], 'COMPLETE')
        self.assertEqual(coverage['rules'], [{'rule_id': 'R1', 'disposition': 'unassigned', 'disposition_source': 'unassigned'}])
        self.assertIn('did not include', payload['legacy-requirements.json']['note'])

    def test_requirement_files_are_copied_and_only_explicit_dispositions_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assessment = root / 'assessment'
            (assessment / 'spec').mkdir(parents=True)
            (assessment / 'spec' / 'requirements.md').write_text('REQ unchanged\n', encoding='utf-8')
            (assessment / 'traceability.yaml').write_text('- id: R1\n  disposition: captured\n', encoding='utf-8')
            (root / 'secret.md').write_text('hidden\n', encoding='utf-8')
            files = handoff.read_legacy_requirements(root, [
                'spec/requirements.md', 'traceability.yaml', '../secret.md', 'spec/../../secret.md'])
        self.assertEqual([item['path'] for item in files], ['spec/requirements.md', 'traceability.yaml'])
        self.assertEqual(files[0]['text'], 'REQ unchanged\n')
        rules = [{'id': 'R1', 'program': 'CLAIMINF'}, {'id': 'R2', 'program': 'CLAIMINF'}]
        coverage = handoff.rule_coverage(rules, files)
        self.assertEqual(coverage['status'], 'COMPLETE')
        self.assertEqual(coverage['rules'][0]['disposition'], 'captured')
        self.assertEqual(coverage['rules'][0]['disposition_source'], 'assessment_traceability')
        self.assertEqual(coverage['rules'][1]['disposition'], 'unassigned')
        prose = [{'path': 'traceability.yaml', 'text': 'The id: R1 disposition: captured appears in a sentence.'}]
        self.assertEqual(handoff.rule_coverage(rules, prose)['rules'][0]['disposition'], 'unassigned')
        listed = [{'path': 'traceability.json', 'text': json.dumps([{'id': 'R2', 'disposition': 'delegated'}])}]
        self.assertEqual(handoff.rule_coverage(rules, listed)['rules'][1]['disposition'], 'delegated')
        self.assertEqual(handoff.rule_coverage([{'id': 'R1'}, {'id': 'R1'}], [])['status'], 'GAP')
