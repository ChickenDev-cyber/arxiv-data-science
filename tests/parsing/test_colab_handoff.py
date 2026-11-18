"""Behavior checks for the notebook's staging, resume and durable handoff."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from arxiv_pipeline.parsing.colab import (
    copy_verified, resolve_team_root, stage_corpus, prepare_run, sync_results,
)


class ColabHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def archive(self, traversal=False):
        archive = self.root / 'm1.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            prefix = 'm1-colab-trong-01/corpus/raw/trong/'
            z.writestr(prefix + '2501-00001/metadata.json', json.dumps({
                'arxiv_id': '2501.00001', 'versions': [{'version': 1}, {'version': 2}]}))
            z.writestr(prefix + '2501-00001/v1/main.tex',
                       r'\documentclass{article}\begin{document}Body.\end{document}')
            z.writestr(prefix + '2501-00001/v2/', '')
            if traversal:
                z.writestr(prefix + '../escape.txt', 'bad')
        return archive

    def test_staging_keeps_empty_versions_and_rejects_existing_destination(self):
        archive = self.archive()
        corpus = self.root / 'corpus'
        receipt = stage_corpus(archive, corpus, 'trong', expected_count=1)
        self.assertEqual(receipt['declared_versions'], 2)
        self.assertTrue((corpus / '2501-00001/v2').is_dir())
        with self.assertRaises(FileExistsError):
            stage_corpus(archive, corpus, 'trong', expected_count=1)
        self.assertTrue((corpus / '2501-00001/v1/main.tex').is_file())

    def test_staging_rejects_archive_path_traversal(self):
        with self.assertRaises(ValueError):
            stage_corpus(self.archive(True), self.root / 'corpus', 'trong', expected_count=1)
        self.assertFalse((self.root / 'escape.txt').exists())

    def test_copy_verifies_destination_and_keeps_previous_generation_on_corruption(self):
        source = self.root / 'source'; source.write_bytes(b'correct')
        target = self.root / 'target'; target.write_bytes(b'previous')
        def corrupt(src, dst):
            Path(dst).write_bytes(b'bad')
        with patch('arxiv_pipeline.parsing.colab.shutil.copyfile', side_effect=corrupt):
            with self.assertRaises(ValueError):
                copy_verified(source, target)
        self.assertEqual(target.read_bytes(), b'previous')
        copy_verified(source, target)
        self.assertEqual(target.read_bytes(), b'correct')
        self.assertEqual(target.with_suffix('.sha256').read_text().strip(),
                         hashlib.sha256(b'correct').hexdigest())

    def test_missing_team_folder_does_not_create_personal_fallback(self):
        mount = self.root / 'drive'; (mount / 'MyDrive').mkdir(parents=True)
        with self.assertRaises(FileNotFoundError):
            resolve_team_root(mount, 'team-id')
        self.assertEqual(list((mount / 'MyDrive').iterdir()), [])

    def test_shortcut_resolves_exact_team_root(self):
        mount = self.root / 'drive'
        team = mount / '.shortcut-targets-by-id/team-id/arXiv-data'; team.mkdir(parents=True)
        self.assertEqual(resolve_team_root(mount, 'team-id'), team)

    def test_prepare_does_not_create_benchmark_output_or_delete_old_work(self):
        corpus = self.root / 'corpus'; corpus.mkdir()
        output = self.root / 'work/output'
        drive = self.root / 'drive/run-01'
        result = prepare_run(corpus, output, drive, owner='trong', run_id='run-01',
                             source_sha='a' * 64, archive_sha='b' * 64)
        self.assertFalse(output.exists())
        self.assertEqual(Path(result['backup_db']), output.parent / 'checkpoints/checkpoint.sqlite')
        output.mkdir(parents=True); marker = output / 'keep'; marker.write_text('saved')
        with self.assertRaises(FileExistsError):
            prepare_run(corpus, output, drive, owner='trong', run_id='run-01',
                        source_sha='a' * 64, archive_sha='b' * 64)
        self.assertEqual(marker.read_text(), 'saved')

    def test_resume_rejects_code_or_input_identity_change(self):
        source = self.root / 'prior/checkpoint.sqlite'; source.parent.mkdir()
        source.write_bytes(b'not used before identity check')
        source.with_suffix('.sqlite.sha256').write_text(hashlib.sha256(source.read_bytes()).hexdigest())
        (source.parent / 'run.json').write_text(json.dumps({
            'owner': 'trong', 'source_sha256': 'a'*64, 'archive_sha256': 'b'*64}))
        corpus = self.root / 'corpus'; corpus.mkdir()
        for code, archive in [('c'*64, 'b'*64), ('a'*64, 'c'*64)]:
            with self.subTest(code=code, archive=archive), self.assertRaises(ValueError):
                prepare_run(corpus, self.root/'output', self.root/'next', owner='trong',
                            run_id='next', source_sha=code, archive_sha=archive, resume_from=source)
        self.assertFalse((self.root/'next').exists())

    def test_sync_retains_statistics_and_logs_with_verified_hashes(self):
        output = self.root/'output'; output.mkdir()
        for name in ['parsing.sqlite', 'benchmark.json', 'pipeline.json', 'stdout.log', 'stderr.log',
                     'statistics/publications.csv', 'statistics/publications.json', 'statistics/summary.json',
                     'parquet/elements.parquet', 'parquet/hierarchy.parquet', 'parquet/bibtex.parquet']:
            path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
        target=self.root/'drive';target.mkdir()
        receipt = sync_results(output,target)
        saved = Path(receipt['directory'])
        self.assertEqual((saved/'statistics/publications.csv').read_text(),'statistics/publications.csv')
        self.assertTrue((saved/'stderr.log.sha256').is_file())
        self.assertEqual(receipt['status'], 'artifacts_saved')
        self.assertFalse((target/'handoff.json').exists())


if __name__ == '__main__':
    unittest.main()
