"""Regression checks for the Colab I/O performance patch."""
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from arxiv_pipeline.benchmark.profiler import ResourceSampler, get_dir_size
from arxiv_pipeline.runner.writer import Publisher, digest


class TestPerfIO(unittest.TestCase):
    def test_scandir_counts_nested_files_and_ignores_links(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'nested').mkdir()
            (root / 'nested' / 'file.bin').write_bytes(b'x' * 500)
            (root / 'other.bin').write_bytes(b'y' * 300)
            (root / 'shortcut').symlink_to(root / 'nested', target_is_directory=True)
            self.assertEqual(get_dir_size(root), 800)

    def test_ram_sampling_does_not_rescan_disk_every_100ms(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'before').write_bytes(b'a')
            with patch('arxiv_pipeline.benchmark.profiler.get_dir_size', wraps=get_dir_size) as scan:
                sampler = ResourceSampler(target_dir=root, interval_seconds=0.01)
                sampler.start()
                time.sleep(0.06)
                (root / 'after').write_bytes(b'b' * 100)
                _, _, peak_disk, _ = sampler.stop()
            self.assertEqual(scan.call_count, 2)
            self.assertEqual(peak_disk, 101)

    def test_publisher_digest_cache_revalidates_changed_file(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            file = root / 'artifact.txt'
            file.write_bytes(b'original')
            publisher = Publisher({'output_root': str(root)}, store=None)
            with patch('arxiv_pipeline.runner.writer.digest', wraps=digest) as sha:
                first = publisher._cached_digest(file)
                self.assertEqual(publisher._cached_digest(file), first)
                self.assertEqual(sha.call_count, 1)
                file.write_bytes(b'modified and longer')
                second = publisher._cached_digest(file)
                self.assertNotEqual(first, second)
                self.assertEqual(sha.call_count, 2)


if __name__ == '__main__':
    unittest.main()
