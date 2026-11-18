"""Regressions for lost Drive checkpoints and validation blocking salvage."""
from contextlib import closing
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from arxiv_pipeline.parsing import colab


class DurableHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/'local/checkpoint.sqlite'
        self.source.parent.mkdir()
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute('CREATE TABLE publications_meta (arxiv_id TEXT)')
            db.execute('CREATE TABLE parsing_checkpoints (arxiv_id TEXT)')
            db.execute("INSERT INTO publications_meta VALUES ('2501.00001')")
            db.execute("INSERT INTO parsing_checkpoints VALUES ('2501.00001')")
        self.write_hash()
        self.drive = self.root/'drive/run'
        self.drive.mkdir(parents=True)
        self.record = {'owner': 'trong', 'source_sha256': 'a'*64,
                       'archive_sha256': 'b'*64}
        (self.drive/'run.json').write_text(json.dumps(self.record))

    def write_hash(self):
        self.source.with_suffix('.sqlite.sha256').write_text(
            hashlib.sha256(self.source.read_bytes()).hexdigest()+'\n')

    def publish(self):
        from arxiv_pipeline.parsing.snapshots import publish_snapshot
        return publish_snapshot(self.source, self.drive/'snapshots', self.record)

    def test_generations_never_overwrite_and_restore_uses_local_sqlite(self):
        from arxiv_pipeline.parsing.snapshots import restore_snapshot
        first = self.publish()
        old = (Path(first['directory'])/'checkpoint.sqlite').read_bytes()
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("INSERT INTO publications_meta VALUES ('2501.00002')")
            db.execute("INSERT INTO parsing_checkpoints VALUES ('2501.00002')")
        self.write_hash()
        second = self.publish()
        self.assertNotEqual(first['directory'], second['directory'])
        self.assertEqual((Path(first['directory'])/'checkpoint.sqlite').read_bytes(), old)
        local = self.root/'restore/checkpoint.sqlite'
        original_connect = sqlite3.connect
        def local_connect(path, *args, **kwargs):
            self.assertNotIn(str(self.drive), str(path), 'SQLite must never open Drive')
            return original_connect(path, *args, **kwargs)
        with patch('sqlite3.connect', side_effect=local_connect):
            restore_snapshot(Path(second['directory'])/'checkpoint.sqlite', local)
        with closing(original_connect(local)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM publications_meta').fetchone()[0], 2)
        self.assertTrue((local.parent/'run.json').is_file())

    def test_corrupt_readback_never_gets_complete_marker(self):
        first = self.publish()
        original = shutil.copyfile
        def corrupt(src, dst, *args, **kwargs):
            result = original(src, dst, *args, **kwargs)
            if Path(dst).is_relative_to(self.drive/'snapshots'):
                Path(dst).write_bytes(b'corrupted by transport')
            return result
        with patch('arxiv_pipeline.parsing.snapshots.shutil.copyfile', side_effect=corrupt):
            with self.assertRaises(ValueError):
                self.publish()
        complete = list((self.drive/'snapshots').glob('*/complete.json'))
        self.assertEqual(complete, [Path(first['directory'])/'complete.json'])

    def test_lost_drive_mount_cannot_publish_a_local_imitation(self):
        self.record['drive_mount'] = str(self.root/'not-mounted')
        with self.assertRaises(RuntimeError):
            self.publish()
        self.assertFalse(list((self.drive/'snapshots').glob('*/complete.json')))

    def test_mismatched_source_hash_is_not_published(self):
        self.source.with_suffix('.sqlite.sha256').write_text('0'*64)
        with self.assertRaises(ValueError):
            self.publish()
        self.assertFalse(list((self.drive/'snapshots').glob('*/complete.json')))

    def test_resume_rejects_generation_without_complete_marker(self):
        from arxiv_pipeline.parsing.snapshots import restore_snapshot
        generation = self.publish()
        (Path(generation['directory'])/'complete.json').unlink()
        with self.assertRaises(ValueError):
            restore_snapshot(Path(generation['directory'])/'checkpoint.sqlite', self.root/'restored.sqlite')
        self.assertFalse((self.root/'restored.sqlite').exists())

    def test_prepare_routes_backup_and_resume_to_local_files(self):
        generation = self.publish()
        corpus = self.root/'corpus'; corpus.mkdir()
        output = self.root/'attempt/output'
        prepared = colab.prepare_run(corpus, output, self.root/'drive/next',
            owner='trong', run_id='next', source_sha='a'*64, archive_sha='b'*64,
            resume_from=Path(generation['directory'])/'checkpoint.sqlite')
        self.assertFalse(output.exists())
        self.assertTrue(Path(prepared['backup_db']).is_relative_to(output.parent))
        command = prepared['command']
        restored = Path(command[command.index('--resume-from')+1])
        self.assertTrue(restored.is_relative_to(output.parent))
        self.assertTrue(restored.is_file())

    def test_validation_failure_still_saves_artifacts_without_handoff(self):
        output = self.root/'output'; output.mkdir()
        shutil.copyfile(self.source, output/'parsing.sqlite')
        (output/'benchmark.json').write_text(json.dumps({'status': 'incomplete'}))
        (output/'stderr.log').write_text('parser failed')
        with self.assertRaises(ValueError):
            colab.finalize_run(self.root/'corpus', output, self.drive, {}, 'a'*64)
        self.assertFalse((self.drive/'handoff.json').exists())
        receipts = list((self.drive/'artifacts').glob('*/artifacts.json'))
        self.assertEqual(len(receipts), 1)
        saved = receipts[0].parent
        self.assertEqual((saved/'parsing.sqlite').read_bytes(), self.source.read_bytes())
        self.assertEqual((saved/'stderr.log').read_text(), 'parser failed')
        self.assertTrue((saved/'validation-error.json').is_file())

    @unittest.skipUnless(importlib.util.find_spec('pyarrow'), 'requires real pyarrow')
    def test_real_fresh_and_resume_complete_with_immutable_backups(self):
        import pyarrow.parquet as pq
        from arxiv_pipeline.parsing.benchmark import source_fingerprint
        corpus = self.root/'corpus'
        ids = ['2501.00001', '2501.00002']
        for aid in ids:
            paper = corpus/aid
            (paper/'v1').mkdir(parents=True)
            (paper/'v2').mkdir()
            (paper/'metadata.json').write_text(json.dumps({
                'arxiv_id': aid, 'versions': [{'version': 1}, {'version': 2}]}))
            (paper/'v1/main.tex').write_text(
                r'\documentclass{article}\begin{document}A sentence.\end{document}')
        identity = source_fingerprint()
        expected = {'arxiv_ids': ids, 'declared_versions': 4}
        env = dict(os.environ, PYTHONPATH=str(Path(colab.__file__).parents[2]),
                   ARXIV_SENTENCE_BACKEND='rule')
        previous = None
        outputs = []
        for name in ['fresh', 'resume']:
            output = self.root/name/'output'
            drive = self.root/'drive'/name
            prepared = colab.prepare_run(corpus, output, drive,
                owner='trong', run_id=name, source_sha=identity, archive_sha='b'*64,
                resume_from=previous, backup_every=1)
            if name == 'fresh':
                with patch('arxiv_pipeline.parsing.snapshots.publish_snapshot', side_effect=OSError('temporary Drive outage')):
                    state = colab.run_prepared(prepared, env=env, poll_seconds=0.02)
                self.assertEqual(state['status'], 'incomplete')
                self.assertFalse((drive/'handoff.json').exists())
            else:
                state = colab.run_prepared(prepared, env=env, poll_seconds=0.02)
                self.assertEqual(state['status'], 'complete', state)
                # A generation can become unreadable AFTER the guardian accepted it.
                (Path(state['snapshots'][-1]['directory'])/'checkpoint.sqlite').write_bytes(b'late corruption')
            result = colab.finalize_run(corpus, output, drive, expected, identity)
            self.assertEqual(result['status'], 'complete')
            self.assertTrue((drive/'handoff.json').is_file())
            self.assertEqual(result['final_snapshot']['publications'], 2)
            previous = Path(result['final_snapshot']['directory'])/'checkpoint.sqlite'
            benchmark = json.loads((output/'benchmark.json').read_text())
            self.assertEqual(benchmark['pipeline']['skipped_publications'], 2 if name=='resume' else 0)
            self.assertEqual(benchmark['pipeline']['processed_publications'], 0 if name=='resume' else 2)
            self.assertEqual(benchmark['pipeline']['failed_publications'], 0)
            outputs.append(output)
            for table in ['elements', 'hierarchy', 'bibtex']:
                local = pq.read_table(output/'parquet'/f'{table}.parquet')
                saved = pq.read_table(Path(result['directory'])/'parquet'/f'{table}.parquet')
                self.assertTrue(local.equals(saved))
        with closing(sqlite3.connect(outputs[0]/'parsing.sqlite')) as fresh, closing(sqlite3.connect(outputs[1]/'parsing.sqlite')) as resumed:
            for table in ['elements', 'hierarchy', 'bibtex', 'publications_meta', 'parsing_checkpoints']:
                self.assertEqual(fresh.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall(),
                                 resumed.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall())


if __name__ == '__main__':
    unittest.main()
