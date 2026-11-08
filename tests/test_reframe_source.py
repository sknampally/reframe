import json
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import reframe_source as source
from pilot import Blocked


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.archive = self.work / 'fixture.zip'

    def archive_with(self, name, content='source'):
        with zipfile.ZipFile(self.archive, 'w') as bundle:
            bundle.writestr(name, content)

    def test_regular_archive_extracts_without_git_metadata(self):
        self.archive_with('app/program.cbl')
        destination = self.work / 'source'
        source.extract_archive(self.archive, destination)
        self.assertEqual((destination / 'app/program.cbl').read_text(), 'source')
        self.assertEqual((destination / 'app/program.cbl').stat().st_mode & 0o777, 0o600)

    def test_unsafe_paths_rejected_before_any_extraction(self):
        for name in ['../outside', '/absolute', 'app/../../outside', '.git/config', 'a\\..\\outside']:
            with self.subTest(name=name):
                self.archive_with(name)
                destination = self.work / 'source'
                with self.assertRaises(Blocked): source.extract_archive(self.archive, destination)
                self.assertFalse(destination.exists())

    def test_symlink_and_expansion_limit_rejected(self):
        item = zipfile.ZipInfo('link')
        item.create_system = 3
        item.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.archive_with(item, '../outside')
        with self.assertRaises(Blocked): source.extract_archive(self.archive, self.work / 'source')
        self.archive_with('large.cbl', '12345')
        with self.assertRaises(Blocked): source.extract_archive(self.archive, self.work / 'source', limit=4)

    def test_download_is_pinned_to_head_etag_without_a_source_commit(self):
        calls = []
        def aws(argv, **kwargs):
            calls.append(argv)
            if 'head-object' in argv:
                return SimpleNamespace(returncode=0, stdout=json.dumps({'ContentLength':100, 'ETag':'fixture-etag'}))
            with zipfile.ZipFile(argv[-1], 'w') as bundle: bundle.writestr('main.cbl', 'source')
            return SimpleNamespace(returncode=0, stdout='{}')
        with patch.object(source.subprocess, 'run', side_effect=aws), patch.object(source, 'git', return_value='fixture-tree') as git:
            record = source.from_s3('s3://fixture-bucket/code.zip', self.work, 'fixture', 'us-east-1')
        self.assertIn('--if-match', calls[1])
        self.assertIn('fixture-etag', calls[1])
        self.assertEqual(record['revision_type'], 'git-index-tree')
        self.assertFalse(any('commit' in call.args for call in git.call_args_list))
