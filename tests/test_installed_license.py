"""License fallback from an installed wheel, outside the source checkout.

The check runs in a fresh interpreter. Importing this file from the unit-test
loader must not make the source tree satisfy the installed-package import.
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


@unittest.skipUnless(os.environ.get('REFRAME_INSTALLED_LICENSE') == '1',
                      'Runs against an installed wheel, outside the source checkout')
class InstalledLicenseTests(unittest.TestCase):
    def test_installed_export_uses_packaged_mit_text(self):
        source = Path(os.environ['REFRAME_LICENSE_SOURCE']).resolve()
        script = textwrap.dedent(
            '''
            import json, sys
            from pathlib import Path
            import reframe_handoff as handoff
            import reframe_optional as optional
            source = Path(sys.argv[1]).resolve()
            installed = Path(handoff.__file__).resolve()
            if source.as_posix() in installed.as_posix():
                raise SystemExit('installed module resolved inside the source checkout')
            if (installed.parents[1] / 'LICENSE').is_file():
                raise SystemExit('checkout LICENSE masked the packaged license')
            license_text = (source / 'LICENSE').read_text()
            notice = handoff.framework_notice()
            if not notice.endswith(license_text):
                raise SystemExit('packaged notice does not end with the repository MIT text')
            if 'does not claim ownership of the source code, AWS findings' not in notice:
                raise SystemExit('notice does not limit ownership to copied framework material')
            root = Path(sys.argv[2])
            version = 'v1'
            preview = root / 'previews' / version
            current = preview / 'current-state'
            current.mkdir(parents=True)
            (preview / 'design.json').write_text(json.dumps({
                'screens': [], 'journeys': [], 'apis': [], 'rules': [], 'questions': [],
                'specification': {'scope': 'fixture', 'rules': [], 'sections': {}, 'work_items': []},
            }))
            (current / 'screens.json').write_text('{"screens":[]}\\n')
            (current / 'api.html').write_text('<p>fixture</p>\\n')
            files = optional._mode_package_files(
                root, {'site': {'directory': 'missing-site'}}, version, 'functional-parity',
                b'<p>current</p>\\n', [])
            if files['reframe-notice.md'].decode() != notice:
                raise SystemExit('export notice differs from the packaged MIT notice')
            if 'copied Reframe framework instructions' not in files['README.md'].decode():
                raise SystemExit('export README does not scope the notice')
            if b'Copyright (c)' in files['specification.json']:
                raise SystemExit('export specification carries the framework copyright')
            print('installed-license-ok')
            ''')
        work = Path(tempfile.mkdtemp(prefix='reframe-installed-export-'))
        self.addCleanup(lambda: __import__('shutil').rmtree(work, ignore_errors=True))
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        completed = subprocess.run(
            [sys.executable, '-c', script, str(source), str(work)],
            cwd=work, env=env, text=True, capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn('installed-license-ok', completed.stdout)


if __name__ == '__main__':
    unittest.main()
