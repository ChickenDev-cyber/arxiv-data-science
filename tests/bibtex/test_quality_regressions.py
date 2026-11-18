"""Regressions reduced from the 200-publication pilot."""
import unittest
from arxiv_pipeline.bibtex.parser import parse_bibitem
from arxiv_pipeline.bibtex.service import extract_and_unify_references
from arxiv_pipeline.bibtex.deduplicator import unify_entries

RAW = (r'K.~Akiyama \textit{et al.} [Event Horizon Telescope], '
       r'Astrophys. J. Lett. \textbf{875}, L1 (2019) '
       r'doi:10.3847/2041-8213/ab0ec7 [arXiv:1906.11238 [astro-ph.GA]].')

class BibliographyQualityTests(unittest.TestCase):
    def test_year_is_not_taken_from_arxiv_identifier(self):
        for identifier in ['1906.11238', '2001.00001v2', '2100.12345']:
            with self.subTest(identifier=identifier):
                entry = parse_bibitem('2505.00280','first',RAW.replace('1906.11238',identifier))
                self.assertEqual(entry['year'],2019)

    def test_arxiv_identifier_alone_does_not_invent_a_publication_year(self):
        entry=parse_bibitem('2505.00280','x',r'A. Author, Unknown work [arXiv:2001.00001].')
        self.assertIsNone(entry['year'])

    def test_journal_only_reference_does_not_invent_a_title(self):
        entry=parse_bibitem('2505.00280','first',RAW)
        self.assertEqual(entry['title'],'')
        self.assertIn('K. Akiyama',entry['authors'])
        self.assertEqual(entry['journal'],'Astrophys. J. Lett.')
        self.assertFalse(any('doi:' in name or 'Astrophys' in name for name in entry['authors']))

    def test_two_eht_references_keep_their_own_keys_and_dois(self):
        other=RAW.replace('L1 (2019)','no.1, L4 (2019)').replace('ab0ec7','ab0e85').replace('1906.11238','1906.11241')
        tex=r'\begin{thebibliography}{9}\bibitem{first}'+RAW+r'\bibitem{fourth}'+other+r'\end{thebibliography}'
        refs=extract_and_unify_references([{'arxiv_id':'2505.00280','version':1,'merged_tex':tex,'bib_files':[],'bbl_files':[]}])
        self.assertEqual(len(refs['unified_bibtex']),2)
        self.assertEqual(refs['citation_remap'][1],{'first':'first','fourth':'fourth'})

    def test_different_dois_override_even_equal_plausible_titles(self):
        entries=[(1,{'citation_key':'a','title':'A Reused Paper Title','doi':'10.1234/one'}),
                 (2,{'citation_key':'b','title':'A Reused Paper Title','doi':'10.1234/two'}),
                 (3,{'citation_key':'c','title':'A Reused Paper Title','doi':'10.1234/one'})]
        unique,remap=unify_entries(entries)
        self.assertEqual(len(unique),2)
        self.assertEqual(remap[3]['c'],'a')

    def test_doi_less_entry_cannot_bridge_conflicting_dois(self):
        entries=[(1,{'citation_key':'a','title':'A Reused Paper Title','doi':'10.1234/one'}),
                 (2,{'citation_key':'b','title':'A Reused Paper Title','doi':'10.1234/two'}),
                 (3,{'citation_key':'uncertain','title':'A Reused Paper Title','doi':''})]
        unique,remap=unify_entries(entries)
        self.assertEqual(len(unique),3)
        self.assertEqual(remap[3]['uncertain'],'uncertain')

    def test_title_match_still_unions_missing_doi_and_author_fields(self):
        entries=[(1,{'citation_key':'a','title':'A Useful Paper Title','doi':'','authors':['A. Author'],'year':2020}),
                 (2,{'citation_key':'b','title':'A Useful Paper Title','doi':'10.1234/one','authors':['B. Author'],'year':None})]
        unique,remap=unify_entries(entries)
        self.assertEqual(len(unique),1)
        self.assertEqual(unique[0]['doi'],'10.1234/one')
        self.assertEqual(unique[0]['authors'],['A. Author','B. Author'])
        self.assertEqual(unique[0]['year'],2020)
        self.assertEqual(remap[2]['b'],'a')
