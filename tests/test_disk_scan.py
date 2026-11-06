"""Disk totals include staging bytes and tolerate ordinary sampling races."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arxiv_pipeline.benchmark.profiler import get_dir_size


class DiskScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_counts_all_regular_bytes_including_hidden_staging_and_scratch(self):
        for name, size in [('raw/a', 7), ('.staging/pending', 11), ('scratch/b', 13), ('zero', 0)]:
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'x'*size)
        self.assertEqual(get_dir_size(self.root), 31)
        self.assertEqual(get_dir_size(self.root/'raw/a'), 7)
        self.assertEqual(get_dir_size(self.root/'missing'), 0)

    def test_skips_symlink_files_directories_broken_links_cycles_and_fifo(self):
        (self.root/'real').write_bytes(b'12345')
        (self.root/'file-link').symlink_to(self.root/'real')
        (self.root/'cycle').symlink_to(self.root, target_is_directory=True)
        (self.root/'missing-link').symlink_to(self.root/'missing')
        os.mkfifo(self.root/'pipe')
        self.assertEqual(get_dir_size(self.root), 5)
        self.assertEqual(get_dir_size(self.root/'cycle'), 0)
        self.assertEqual(get_dir_size(self.root/'file-link'), 0)
        self.assertEqual(get_dir_size(self.root/'pipe'), 0)

    def test_deep_tree_does_not_depend_on_python_recursion_limit(self):
        current = self.root
        parents = []
        try:
            for _ in range(1100):
                current = current/'d'
                current.mkdir()
                parents.append(current)
            (current/'leaf').write_bytes(b'abc')
            self.assertEqual(get_dir_size(self.root), 3)
        finally:
            (current/'leaf').unlink(missing_ok=True)
            for path in reversed(parents):
                path.rmdir()

    def test_inaccessible_directory_does_not_drop_accessible_siblings(self):
        for name in ['blocked', 'good']:
            (self.root/name).mkdir()
            (self.root/name/'file').write_bytes(b'abc')
        real_scan = os.scandir
        def scan(path):
            if Path(path).name == 'blocked':
                raise PermissionError('synthetic unreadable directory')
            return real_scan(path)
        with patch('os.scandir', side_effect=scan):
            self.assertEqual(get_dir_size(self.root), 3)

    def test_disappearing_entry_does_not_drop_other_entries(self):
        (self.root/'good').write_bytes(b'abc')
        class Vanished:
            path = str(self.root/'gone')
            name = 'gone'
            def is_symlink(self): return False
            def is_file(self, **kwargs): return False
            def is_dir(self, **kwargs):
                raise FileNotFoundError('synthetic race')
        # Root scan uses an entry that disappears before any type/stat check.
        real_scan = os.scandir
        with real_scan(self.root) as actual:
            good = list(actual)
        class Entries:
            def __enter__(inner): return iter([Vanished(), *good])
            def __exit__(inner, *args): pass
        with patch('arxiv_pipeline.benchmark.profiler.os.scandir', return_value=Entries()):
            self.assertEqual(get_dir_size(self.root), 3)
