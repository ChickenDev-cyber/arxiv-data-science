"""Freeze local M2 Python/resources without committing, pushing or packing data.

Usage: python tools/build_m2_release.py --output-dir /path/to/new-release
The output contains code ZIP/checksum and a notebook pinned to its source SHA.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[1]
    package=repo/'src/arxiv_pipeline'
    digest=hashlib.sha256()
    for path in sorted(package.rglob('*.py')):
        digest.update(path.relative_to(package).as_posix().encode()+b'\0')
        digest.update(path.read_bytes())
    source_sha=digest.hexdigest()
    output=args.output_dir
    output.mkdir(parents=True,exist_ok=False)
    archive=output/f'm2-code-{source_sha}.zip'
    files={}
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for path in sorted(package.rglob('*')):
            if path.is_file() and path.suffix in {'.py','.txt'} and '__pycache__' not in path.parts:
                name=path.relative_to(repo).as_posix()
                z.write(path,name)
                files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        z.writestr('src/requirements-m2.txt','psutil==5.9.5\npyarrow==23.0.1\n')
        z.writestr('release.json',json.dumps({'source_sha256':source_sha,'files':files},indent=2))
    with archive.open('rb') as stream:archive_sha=hashlib.file_digest(stream,'sha256').hexdigest()
    archive.with_suffix('.zip.sha256').write_text(archive_sha+'\n')
    notebook=repo/'src/m2_colab_runbook.ipynb'
    nb=json.loads(notebook.read_text())
    config=next(c for c in nb['cells'] if c.get('id')=='config')
    config['source']=re.sub(r"EXPECTED_SOURCE_SHA256 = '[^']*'",
                           f"EXPECTED_SOURCE_SHA256 = '{source_sha}'",''.join(config['source'])).splitlines(keepends=True)
    text=json.dumps(nb,ensure_ascii=False,indent=2)+'\n'
    notebook.write_text(text)
    (output/notebook.name).write_text(text)
    (output/'release.json').write_text(json.dumps({'source_sha256':source_sha,'archive_sha256':archive_sha,
         'archive':archive.name,'files':files},indent=2))
    print(json.dumps({'source_sha256':source_sha,'archive_sha256':archive_sha,'output':str(output)},indent=2))


if __name__=='__main__':
    main()
