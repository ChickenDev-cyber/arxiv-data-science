"""Boundary and external-input cases for the shared arXiv ID rules."""

import unittest

from arxiv_pipeline.identifiers import is_valid_arxiv_id, normalize_arxiv_id


class IdentifierTests(unittest.TestCase):
    def test_canonical_ids_at_scheme_boundaries(self):
        for value in ("0704.0001", "1412.9999", "1501.00001", "2501.12642",
                      "hep-th/9108001", "hep-th/0001001", "math/0309136", "cond-mat/0703999"):
            with self.subTest(value=value):
                self.assertTrue(is_valid_arxiv_id(value))
                self.assertEqual(normalize_arxiv_id(value), value)

    def test_invalid_canonical_ids(self):
        for value in (None, 2501.12642, True, "", " 2501.12642", "2501.12642\n",
                      "arXiv:2501.12642", "2501.12642v1", "https://arxiv.org/abs/2501.12642",
                      "2500.12642", "2513.12642", "2501.00000", "2501.1264", "1412.00001",
                      "0703.0001", "1501.0001", "hep-th/9107001", "hep-th/0704001",
                      "hep-th/9913001", "hep-th/9900001", "hep-th/9901000", "HEP-TH/9901001",
                      "math.GT/0309136", "２５０１.１２６４２", "2501.١٢٦٤٢", "hep-th/９９０１００１"):
            with self.subTest(value=value):
                self.assertFalse(is_valid_arxiv_id(value))

    def test_external_display_ids_normalize_to_canonical_ids(self):
        cases = {
            " arXiv:2501.12642v3 ": "2501.12642",
            "ARXIV:0704.0001v1": "0704.0001",
            " hep-th/9901001v2 ": "hep-th/9901001",
            "arXiv:math.GT/0309136v2": "math/0309136",
            "astro-ph.GA/0703001": "astro-ph/0703001",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                actual = normalize_arxiv_id(value)
                self.assertEqual(actual, expected)
                self.assertTrue(is_valid_arxiv_id(actual))

    def test_normalization_does_not_repair_invalid_ids(self):
        for value in (None, 2501.12642, "", "https://arxiv.org/abs/2501.12642",
                      "arXiv:2513.12642v2", "2500.00001v1", "2501.00000v1", "2501.1264v1",
                      "1412.00001", "0703.0001", "math.GT/0704001v2", "hep-th/9107001v1",
                      "hep-th/9901000", "2501.12642v0", "2501.12642v01", "2501.12642v1v2",
                      "arXiv:arXiv:2501.12642", "../../2501.12642", "２５０１.１２６４２v1"):
            with self.subTest(value=value):
                self.assertIsNone(normalize_arxiv_id(value))


if __name__ == "__main__":
    unittest.main()
