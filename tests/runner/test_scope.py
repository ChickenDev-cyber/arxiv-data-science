from pathlib import Path
import json
import unittest
from arxiv_pipeline.runner.models import RunOptions, paper_spec, config_fingerprint
from arxiv_pipeline.runner.scope import initial_scope, select_papers, source_specs, update_scope
from .helpers import PAPER, metadata_result, discovery_result


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.options = RunOptions("mock-demo", Path("/tmp/test"), ("trong",), True)

    def test_selection_and_partial_versions(self):
        scope = initial_scope(self.options)
        self.assertFalse(scope["scope"]["versions_complete"])
        selected = select_papers(discovery_result(10), self.options)
        self.assertEqual(len(selected), 5)
        self.assertEqual(selected[0], PAPER)
        self.assertEqual([s.version for s in source_specs(PAPER, metadata_result(True))], [1, 2])
        self.assertEqual(json.loads(paper_spec(PAPER, "sources", 1).key),
                         ["trong", "sources", "2504.17523", 1])
        scope = update_scope(scope, [PAPER], [metadata_result()])
        self.assertEqual(scope["scope"]["paper_count"], 1)
        self.assertTrue(scope["scope"]["versions_complete"])

    def test_duplicate_wrong_range_rejected(self):
        for papers in ([PAPER, PAPER], [{"owner": "trong", "arxiv_id": "2501.12642"}]):
            r = discovery_result(); r["data"]["papers"] = papers
            with self.assertRaises(ValueError):
                select_papers(r, self.options)

    def test_fingerprint_is_order_independent_and_mode_sensitive(self):
        a = RunOptions("x", Path("/tmp/test"), ("trong", "khang"), True)
        b = RunOptions("x", Path("/tmp/test"), ("khang", "trong"), True)
        c = RunOptions("x", Path("/tmp/test"), ("khang", "trong"), False)
        self.assertEqual(config_fingerprint(a), config_fingerprint(b))
        self.assertNotEqual(config_fingerprint(a), config_fingerprint(c))
