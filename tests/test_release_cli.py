"""CLI journeys for the optional design commands. No AWS and no model calls."""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import reframe


SCREENS = {
    'journeys': [{'id': 'pay', 'name': 'Pay', 'summary': 'Check the amount.', 'screen_ids': ['s1']}],
    'screens': [{
        'id': 's1', 'title': 'Amount', 'purpose': 'Check the amount before payment.',
        'rule_ids': ['R1'], 'fields': ['Amount: whole dollars'], 'actions': ['Save'],
        'states': ['Normal', 'Amount rejected'], 'uncertainties': 'Validation limits are not recorded.',
        'api_ids': ['validate-amount'],
    }],
    'apis': [{
        'id': 'validate-amount', 'method': 'POST', 'path': '/payments/validate',
        'purpose': 'Check the amount. Legacy fact: the recorded rule rejects zero.',
        'authentication': 'unknown', 'uncertainties': 'Auth scheme is not recorded.',
        'request': {'amount': '1.00'}, 'responses': {'200': 'Accepted'},
    }],
}


class ReleaseCliTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        site = self.root / 'previews' / 'business-1'
        site.mkdir(parents=True)
        (site / 'screens.json').write_text(json.dumps(SCREENS))
        (site / 'index.html').write_text('legacy-review')
        draft = self.root / 'previews' / 'draft'
        draft.mkdir()
        (draft / 'index.html').write_text('skip-draft')
        (self.root / 'packages').mkdir()
        (self.root / 'packages' / 'package-legacy.zip').write_bytes(b'legacy-zip')
        (self.root / 'project.json').write_text(json.dumps({
            'stage': 'PACKAGED', 'scope': 'Synthetic payment check', 'aws': {}, 'assessments': {},
            'source': {'repository': 'https://github.com/example/fixture-mainframe.git', 'commit': 'a' * 40},
            'site': {'directory': 'previews/business-1'}, 'decisions': {},
            'package': {'path': 'packages/package-legacy.zip'},
        }))

    def run_cli(self, *args):
        return reframe.main(['--workspace', str(self.root), *args], reporter=lambda value: setattr(self, 'result', value))

    def test_source_only_parity_package_and_registry_choices(self):
        self.assertEqual(self.run_cli('context', 'status'), 0)
        self.assertEqual(self.result['status'], 'not_provided')
        self.assertEqual(self.run_cli('design-export', '--version', 'v1', '--mode', 'functional-parity'), 0)
        self.assertEqual(self.run_cli('package-export', '--version', 'v1', '--mode', 'functional-parity'), 0)
        self.assertEqual(self.result['final_handoff'], 'NOT_REQUESTED')
        archive = self.root / self.result['archive']
        with zipfile.ZipFile(archive) as bundle:
            manifest = json.loads(bundle.read('manifest.json'))
            self.assertEqual(manifest['package_status'], 'DRAFT')
            document = bundle.read('openapi.json').decode()
            self.assertIn('/payments/validate', document)
            self.assertIn('Draft review APIs', document)
            self.assertNotIn('Approved review', document)
            self.assertNotIn('<script', bundle.read('current-state.html').decode())
        self.assertEqual((self.root / 'previews' / 'business-1' / 'index.html').read_text(), 'legacy-review')
        self.assertEqual((self.root / 'previews' / 'draft' / 'index.html').read_text(), 'skip-draft')
        self.assertEqual((self.root / 'packages' / 'package-legacy.zip').read_bytes(), b'legacy-zip')
        self.assertEqual(self.run_cli('design-export', '--version', 'draft', '--mode', 'functional-parity'), 2)
        self.assertEqual(self.run_cli('register-project'), 0)
        self.assertEqual(self.result['status'], 'REGISTERED')
        saved = json.loads((self.root / 'project.json').read_text())
        saved['source']['commit'] = 'b' * 40
        (self.root / 'project.json').write_text(json.dumps(saved))
        self.assertEqual(self.run_cli('register-project'), 2)
        self.assertEqual(self.run_cli('register-project', '--choice', 'assess-new-revision'), 0)
        registry = json.loads((self.root / 'project-registry.json').read_text())
        self.assertEqual(registry['aws_jobs_created'], 0)
        self.assertIsNone(registry['runs'][-1]['aws_job'])
        self.assertIn('reframe_optional', (Path(__file__).parents[1] / 'pyproject.toml').read_text())
