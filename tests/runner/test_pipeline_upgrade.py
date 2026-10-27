from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from arxiv_pipeline.discovery.api import RemoteError
from arxiv_pipeline.discovery.service import DiscoveryModule
from arxiv_pipeline.runner.cli import main
from arxiv_pipeline.runner.factory import MockModuleFactory
from arxiv_pipeline.runner.mock import make_mock_modules
from arxiv_pipeline.runner.models import RunOptions, paper_spec
from arxiv_pipeline.runner.restore import RunStateRelocator
from arxiv_pipeline.runner.service import PipelineRunner, run_pipeline
from arxiv_pipeline.runner.state import StateStore, load_options
from .helpers import make_v1_corpus


class PipelineUpgradeTests(unittest.TestCase):
    def test_invalid_saved_scope_cannot_leave_ready_marker_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = make_v1_corpus(root)
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(root / 'state.sqlite')) as connection:
                scope = json.loads(connection.execute('SELECT scope_json FROM runs').fetchone()[0])
                scope['scope']['ranges'] = []
                connection.execute('UPDATE runs SET scope_json=?', (json.dumps(scope),))
                connection.commit()
            try:
                run_pipeline(options, make_mock_modules(options))
            except Exception as exc:
                self.assertIsInstance(exc, ValueError)
            else:
                self.fail('A stored scope outside configured ranges must be rejected')
            self.assertFalse(json.loads((root / 'runs/legacy/validation.json').read_text())['valid'])

    def test_mock_sources_then_references_preserves_scope(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as out, \
             redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--mock', '--all-owners', '--run-id', 'phases', '--output-root', tmp,
                                   '--stage', 'sources']), 2)
            scope_path = Path(tmp) / 'runs/phases/scope.json'
            before = json.loads(scope_path.read_text())
            self.assertTrue(before['scope']['versions_complete'])
            self.assertEqual(before['scope']['paper_count'], 5)
            self.assertFalse(list((Path(tmp) / 'raw').rglob('references.json')))
            self.assertEqual(main(['--resume', '--run-id', 'phases', '--output-root', tmp,
                                   '--stage', 'references']), 0)
            self.assertEqual(json.loads(scope_path.read_text()), before)
            self.assertEqual(len(list((Path(tmp) / 'raw').rglob('references.json'))), 5)
            out.seek(0); out.truncate(0)
            self.assertEqual(main(['--resume', '--run-id', 'phases', '--output-root', tmp]), 0)
            self.assertEqual(json.loads(out.getvalue())['executed_tasks'], 0)

    def test_migrate_restore_resume_does_not_redownload_good_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            original, restored = Path(tmp) / 'original', Path(tmp) / 'copy'
            make_v1_corpus(original)
            original_bytes = (original / 'state.sqlite').read_bytes()
            shutil.copytree(original, restored)
            RunStateRelocator().relocate(restored, 'legacy', str(original), snapshot_id='copied-legacy')
            options = load_options(restored / 'state.sqlite', 'legacy')
            modules = make_mock_modules(options)
            with patch.object(modules.sources, 'fetch_sources', side_effect=AssertionError('must reuse source')):
                outcome = run_pipeline(options, modules)
            self.assertEqual(outcome.executed_tasks, 0)
            self.assertEqual(outcome.reused_tasks, 5)
            self.assertTrue(outcome.quality['valid'])
            self.assertEqual((original / 'state.sqlite').read_bytes(), original_bytes)

    def test_interruption_and_invalid_result_preserve_previous_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            options = RunOptions('invalid-retry', root, ('trong',), True)
            modules = make_mock_modules(options)
            original = modules.references.reader.references_page

            def partial(aid, offset, limit):
                if offset:
                    raise RemoteError('network_error', 'page interrupted', True)
                return {'data': original(aid, 0, limit)['data'][:1], 'next': 1}

            with patch.object(modules.references.reader, 'references_page', side_effect=partial):
                first = run_pipeline(options, modules)
            self.assertEqual(first.exit_code, 2)
            path = root / 'raw/trong/2504-17523/raw_references.json'
            before_bytes = path.read_bytes()
            key = paper_spec({'owner': 'trong', 'arxiv_id': '2504.17523'}, 'references').key
            with StateStore.readonly(root / 'state.sqlite', options.run_id) as store:
                before_result = store.get_task(key)['accepted_result']
            invalid = {**before_result, 'arxiv_id': '2504.17524'}
            with patch.object(modules.references, 'fetch_references', return_value=invalid):
                with self.assertRaisesRegex(ValueError, 'invalid Result'):
                    run_pipeline(options, modules, stage='references')
            self.assertEqual(path.read_bytes(), before_bytes)
            with StateStore.readonly(root / 'state.sqlite', options.run_id) as store:
                self.assertEqual(store.get_task(key)['accepted_result'], before_result)
            marker = json.loads((root / 'runs/invalid-retry/validation.json').read_text())
            self.assertFalse(marker['valid'])
            self.assertEqual(run_pipeline(options, modules).exit_code, 0)

    def test_interrupted_discovery_checkpoint_can_be_restored_and_resumed(self):
        class Query:
            attempts = 0

            def __init__(self):
                self.calls = []
                self.interrupt = True

            def query(self, ids):
                self.calls.append(list(ids))
                self.attempts += 1
                if len(self.calls) == 2 and self.interrupt:
                    raise KeyboardInterrupt()
                aid = ids[0]
                return (f'<feed xmlns="http://www.w3.org/2005/Atom" '
                        f'xmlns:o="http://a9.com/-/spec/opensearch/1.1/">'
                        f'<o:totalResults>1</o:totalResults><o:startIndex>0</o:startIndex>'
                        f'<entry><id>https://arxiv.org/abs/{aid}</id></entry></feed>').encode()

        query = Query()

        class Factory(MockModuleFactory):
            def build(self, options, checkpoints):
                modules = super().build(options, checkpoints)
                return replace(modules, discovery=DiscoveryModule(
                    query, batch_size=1, max_candidates=2, checkpoints=checkpoints))

        with tempfile.TemporaryDirectory() as tmp:
            origin, restored = Path(tmp) / 'original', Path(tmp) / 'copy'
            options = RunOptions('interrupted', origin, ('trong',), True, max_candidates=2)
            paused = PipelineRunner(Factory()).run(options, stage='discovery')
            self.assertEqual(paused.exit_code, 130)
            self.assertTrue(paused.validation['valid'])
            shutil.copytree(origin, restored)
            try:
                RunStateRelocator().relocate(restored, 'interrupted', str(origin), snapshot_id='paused-copy')
            except ValueError as exc:
                self.fail(f'A stopped checkpoint with interrupted work must be restorable: {exc}')
            query.interrupt = False
            count_before = len(query.calls)
            result = PipelineRunner(Factory()).run(
                load_options(restored / 'state.sqlite', 'interrupted'), stage='discovery')
            self.assertEqual(query.calls[count_before:], [['2504.17524']])
            self.assertEqual(result.scope['scope']['paper_count'], 2)
            self.assertTrue(result.validation['valid'])

    def test_resume_other_stage_can_leave_interrupted_source_work_for_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            origin, restored = Path(tmp) / 'original', Path(tmp) / 'copy'
            options = RunOptions('switch-after-interrupt', origin, ('trong',), True)
            modules = make_mock_modules(options)
            with patch.object(modules.sources, 'fetch_sources', side_effect=KeyboardInterrupt):
                self.assertEqual(run_pipeline(options, modules, stage='sources').exit_code, 130)
            refs = run_pipeline(options, modules, stage='references')
            self.assertEqual(refs.exit_code, 2)
            self.assertTrue(refs.validation['valid'])
            shutil.copytree(origin, restored)
            try:
                RunStateRelocator().relocate(restored, options.run_id, str(origin), snapshot_id='later-source')
            except ValueError as exc:
                self.fail(f'An inactive source task must not block copying a finished references stage: {exc}')
            new_options = load_options(restored / 'state.sqlite', options.run_id)
            self.assertEqual(run_pipeline(new_options, make_mock_modules(new_options)).exit_code, 0)

