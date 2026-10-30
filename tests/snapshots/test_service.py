"""The snapshot facade preserves its dependency and limit contracts."""
import unittest
from arxiv_pipeline.snapshots.common import SnapshotLimits, check_manifest


class SnapshotConfigurationTests(unittest.TestCase):
    def test_resource_limits_reject_unusable_values(self):
        for name in ('max_files', 'max_file_bytes', 'max_total_bytes'):
            for value in (True, 0, -1, 1.5):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    SnapshotLimits(**{name: value})

    def test_rejected_manifest_cannot_pass_the_facade(self):
        with self.assertRaisesRegex(ValueError, 'wrong identity'):
            check_manifest({}, lambda value: ['wrong identity'])

    def test_validator_contract_is_enforced(self):
        with self.assertRaises(TypeError):
            check_manifest({}, lambda value: None)


if __name__ == '__main__':
    unittest.main()
