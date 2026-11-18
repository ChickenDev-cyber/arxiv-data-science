"""Statistics required by parsing_new.md must use real counts and denominators."""
import json
from pathlib import Path
import tempfile
import unittest

from arxiv_pipeline.parsing.runner import run_parsing_pipeline


class RequiredStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.corpus=self.root/'corpus';self.paper=self.corpus/'2501.00001'
        for version in [1,2,3]: (self.paper/f'v{version}').mkdir(parents=True)
        (self.paper/'metadata.json').write_text(json.dumps({'arxiv_id':'2501.00001','versions':[{'version':i} for i in [1,2,3]]}))
        bib='@article{same,title={A sufficiently long title},author={Doe, Jane},year={2020}}\n'
        for version in [1,2]:
            d=self.paper/f'v{version}'
            (d/'main.tex').write_text(r'\documentclass{article}\begin{document}Shared prose.\cite{same}\bibliography{refs}\end{document}')
            (d/'refs.bib').write_text(bib*(2 if version==1 else 1))
        self.db=self.root/'out.sqlite'

    def test_bibtex_before_count_includes_same_key_duplicates_in_one_version(self):
        stats=run_parsing_pipeline(self.corpus,self.db)
        self.assertEqual(stats['bibtex_before_dedup'],3)
        self.assertEqual(stats['bibtex_after_dedup'],1)

    def test_per_publication_coverage_accounts_for_empty_versions(self):
        from arxiv_pipeline.parsing.statistics import export_statistics
        run_parsing_pipeline(self.corpus,self.db)
        summary=export_statistics(self.corpus,self.db,self.root/'stats')
        rows=json.loads((self.root/'stats'/'publications.json').read_text())
        self.assertEqual(rows[0]['parsed_versions'],[1,2])
        self.assertEqual(rows[0]['empty_versions'],[3])
        self.assertEqual(rows[0]['unaccounted_versions'],[])
        self.assertEqual(summary['execution_success_rate'],1.0)
        self.assertEqual(summary['version_accounted_rate'],1.0)
        self.assertAlmostEqual(summary['parsed_version_fraction'],2/3)

    def test_missing_output_is_counted_as_failure_not_silently_dropped(self):
        from arxiv_pipeline.parsing.statistics import export_statistics
        run_parsing_pipeline(self.corpus,self.db)
        missing=self.corpus/'2501.00002';missing.mkdir()
        (missing/'metadata.json').write_text(json.dumps({'arxiv_id':'2501.00002','versions':[{'version':1}]}))
        summary=export_statistics(self.corpus,self.db,self.root/'stats')
        self.assertEqual(summary['attempted_publications'],2)
        self.assertEqual(summary['execution_success_rate'],0.5)
        self.assertEqual(summary['version_accounted_rate'],0.75)
