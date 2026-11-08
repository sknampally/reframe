import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_reframe as fixtures
import reframe as app
import reframe_setup as setup
import reframe_workspace as layout
from pilot import Blocked, sha


class WorkspaceTests(unittest.TestCase):
    args=fixtures.PreparationTests.args
    assess=fixtures.PreparationTests.assess
    approve=fixtures.PreparationTests.approve
    baseline=fixtures.PreparationTests.baseline

    def setUp(self):
        fixtures.PreparationTests.setUp(self)
        self.baseline()
        mock=patch.object(setup,'active_file',return_value=self.root/'config/active.json')
        mock.start();self.addCleanup(mock.stop)

    def test_source_names_cover_url_local_windows_and_archive(self):
        cases={'https://github.com/owner/carddemo.git':'carddemo', '/projects/payments':'payments',
               r'C:\work\Payments':'Payments','s3://bucket/source.zip':'source'}
        for source,expected in cases.items():self.assertEqual(layout.repository_name(source),expected)
        self.assertEqual(layout.output_directory(self.root,'https://github.com/owner/carddemo.git').name,'carddemo_assessment')

    def test_relocation_preserves_evidence_and_approvals_and_organizes_previews(self):
        old=self.work
        original_approvals=copy.deepcopy(self.project['approvals'])
        run_path=Path(self.project['assessments']['a']['directory'])
        evidence=run_path/'assessment'/'rules.json';evidence_hash=sha(evidence)
        legacy=old/'preview-demo';legacy.mkdir()
        (legacy/'handoff').mkdir();(legacy/'handoff/index.html').write_text('fixture preview')
        (legacy/'ai-dlc-draft.zip').write_bytes(b'fixture zip bytes')
        archive_hash=sha(legacy/'ai-dlc-draft.zip')
        app.write(legacy/'preview-state.json',{'archive_sha256':archive_hash,'status':'DRAFT_READY','calls':1})
        receipt=run_path/'provider-request-1.json';app.write(receipt,{'historical_path':str(old/'source')});receipt_hash=sha(receipt)
        result=layout.relocate(old,self.project,self.root/'engineer-workspace',app)
        target=Path(result['workspace']);moved=app.read(target/'project.json')
        self.assertFalse(old.exists())
        self.assertEqual(setup.workspace(),target)
        self.assertEqual(moved['approvals'],original_approvals)
        new_run=Path(moved['assessments']['a']['directory'])
        self.assertEqual(sha(new_run/'assessment/rules.json'),evidence_hash)
        self.assertEqual(sha(new_run/'provider-request-1.json'),receipt_hash)
        state=app.read(target/'previews/demo/preview-state.json')
        self.assertEqual(sha(target/state['archive_path']),archive_hash)
        self.assertEqual(state['calls'],1)
        self.assertTrue((target/'reports/index.html').exists())
        app.verified_assessment(moved,'a')

    def test_existing_destination_is_never_overwritten(self):
        parent=self.root/'parent';target=layout.output_directory(parent,self.project['source']['repository']);target.mkdir(parents=True)
        with self.assertRaisesRegex(Blocked,'already exists'):layout.relocate(self.work,self.project,parent,app)
        self.assertTrue((self.work/'project.json').exists())

    def test_copy_failure_retains_original_and_active_selection(self):
        setup.select_workspace(self.work)
        with patch.object(layout.shutil,'copytree',side_effect=OSError('fixture disk failure')):
            with self.assertRaises(OSError):layout.relocate(self.work,self.project,self.root/'parent',app)
        self.assertTrue((self.work/'project.json').exists())
        self.assertEqual(setup.workspace(),self.work.resolve())

    def test_managed_source_rebase_preserves_external_and_historical_values(self):
        value={'source':{'root':str(self.work/'source')},'external':'/outside/source',
               'note':'Historical location '+str(self.work)}
        result=layout.rebase(value,self.work,self.root/'new')
        self.assertEqual(result['source']['root'],str(self.root/'new/source'))
        self.assertEqual(result['external'],value['external'])
        self.assertEqual(result['note'],value['note'])

    def test_active_agent_state_blocks_relocation(self):
        self.project['stage']='RUNNING_EXECUTOR'
        with self.assertRaisesRegex(Blocked,'Stop active'):layout.relocate(self.work,self.project,self.root/'parent',app)

    def test_new_preview_path_and_legacy_compatibility(self):
        self.assertEqual(layout.preview_directory(self.work,'new'),self.work/'previews/new')
        old=self.work/'preview-old';old.mkdir()
        self.assertEqual(layout.preview_directory(self.work,'old'),old)

    def test_scripted_init_derives_folder_without_using_saved_selection(self):
        observed=[]
        def initialize(args,work):
            observed.append(work);return {'status':'INITIALIZED'}
        with patch.object(app,'initialize',side_effect=initialize):
            app.main(['init','--output-parent',str(self.root/'workspace'),'--source','https://github.com/example/app.git','--scope','Full repository'],reporter=lambda r:None)
        self.assertEqual(observed,[(self.root/'workspace/app_assessment').resolve()])
