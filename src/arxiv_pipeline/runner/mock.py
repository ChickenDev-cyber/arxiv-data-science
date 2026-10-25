"""Synthetic external adapters; the real T1–T4 services build every Result."""
from datetime import datetime,timedelta,timezone
from email.utils import format_datetime
import io
import tarfile
from ..config import ASSIGNED_RANGES
from ..discovery.service import DiscoveryModule
from ..metadata.service import MetadataModule
from ..references.service import ReferencesModule
from ..sources.service import SourcesModule
from ..sources.extractor import SourceExtractor
from .models import ModuleSet


def _paper(config):
    return config['start_month'][2:].replace('-','')+f".{config['start_id']:05d}"


class _Query:
    attempts=0
    def query(self,ids):
        found=[_paper(c) for c in ASSIGNED_RANGES if _paper(c) in ids]
        entries=''.join(f'<entry><id>https://arxiv.org/abs/{i}v2</id></entry>' for i in found)
        return (f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/">'
                f'<o:totalResults>{len(found)}</o:totalResults><o:startIndex>0</o:startIndex>{entries}</feed>').encode()


class _Record:
    attempts=0
    def record(self,arxiv_id):
        date=datetime(2000+int(arxiv_id[:2]),int(arxiv_id[2:4]),1,tzinfo=timezone.utc)
        versions=''.join(f'<version version="v{v}"><date>{format_datetime(date+timedelta(days=v-1))}</date></version>' for v in (1,2))
        return (f'<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
                f'<request identifier="oai:arXiv.org:{arxiv_id}"/>'
                f'<GetRecord><record><header><identifier>oai:arXiv.org:{arxiv_id}</identifier></header>'
                f'<metadata><arXivRaw xmlns="http://arxiv.org/OAI/arXivRaw/"><id>{arxiv_id}</id>{versions}'
                '<title>SYNTHETIC T5 example</title><authors>Synthetic Author</authors>'
                '</arXivRaw></metadata></record></GetRecord></OAI-PMH>').encode()
    def provenance(self,arxiv_id):return {'source':'synthetic-arXivRaw','fixture':'t5-mock'}


class _References:
    attempts=0
    def references_page(self,arxiv_id,offset,limit):
        items=[]
        if arxiv_id!='2503.06914':
            items.append({'citedPaper':{'paperId':'SYNTHETIC-S2','title':'SYNTHETIC reference','authors':[{'name':'Synthetic Author'}],
                                        'externalIds':{'ArXiv':'2001.00001'}}})
        items.append({'citedPaper':{'paperId':'SYNTHETIC-DOI','title':'SYNTHETIC non-arXiv reference','authors':[{'name':'Synthetic Author'}],
                                    'externalIds':{'DOI':'10.0000/synthetic'}}})
        return {'data':items}


class _Dates:
    attempts=0
    def submission_dates(self,ids):return {i:'2020-01-01' for i in ids}


class _Sources:
    attempts=0
    def download(self,arxiv_id,version,destination):
        tex=(f'% SYNTHETIC ONLY: {arxiv_id}v{version}\n'
             '\\documentclass{article}\n\\begin{document}\n'
             '\\includegraphics{figure.png}\n\\bibliography{refs}\n\\end{document}\n').encode()
        members={'main.tex':tex,'refs.bib':b'@article{example,title={Synthetic}}\n',
                 'figure.png':b'NOT A REAL IMAGE','extra.sty':b'sty','extra.cls':b'cls','extra.bbl':b'bbl'}
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w:gz') as tar:
            for name,data in members.items():
                entry=tarfile.TarInfo(name);entry.size=len(data);tar.addfile(entry,io.BytesIO(data))
        destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(buffer.getvalue())
        return destination.stat().st_size
    def provenance(self,arxiv_id,version):return {'source':'synthetic-source','fixture':'t5-mock'}


def make_mock_modules(options):
    return ModuleSet(DiscoveryModule(_Query(),max_candidates=options.max_candidates),MetadataModule(_Record()),
                     SourcesModule(_Sources(),SourceExtractor()),ReferencesModule(_References(),_Dates()))
