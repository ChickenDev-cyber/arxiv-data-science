"""Exercise the notebook Git bootstrap against a local repository, offline."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class NotebookGitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.repo = Path(__file__).resolve().parents[2]
        cls.fixture = cls.root/'repository'
        (cls.fixture/'src').mkdir(parents=True)
        shutil.copytree(cls.repo/'src/arxiv_pipeline', cls.fixture/'src/arxiv_pipeline',
                        ignore=shutil.ignore_patterns('__pycache__'))
        for cmd in [
            ['git', 'init', str(cls.fixture)],
            ['git', '-C', str(cls.fixture), 'add', 'src'],
            ['git', '-C', str(cls.fixture), '-c', 'user.name=Notebook Test',
             '-c', 'user.email=notebook-test@example.invalid', 'commit', '-m', 'Temporary fixture'],
        ]:
            subprocess.run(cmd, check=True, capture_output=True)
        cls.commit = subprocess.run(['git', '-C', str(cls.fixture), 'rev-parse', 'HEAD'],
                                    check=True, capture_output=True, text=True).stdout.strip()

    def execute_bootstrap(self, name, commit):
        # A clean interpreter represents a new notebook kernel. Only the remote
        # source is replaced; clone/checkout/rev-parse run against real Git.
        script = r'''
from pathlib import Path
import json,subprocess,sys
from unittest.mock import patch
notebook,fixture,work,commit=sys.argv[1:]
work=Path(work);mount=work/'drive';team=mount/'MyDrive/arXiv-data'
team.mkdir(parents=True)
nb=json.loads(Path(notebook).read_text())
cells={c['id']:''.join(c['source']) for c in nb['cells'] if c['cell_type']=='code'}
ns={};exec(compile(cells['config'],'notebook:config','exec'),ns)
assert ns['CODE_MODE']=='commit', 'Git clone must be the default'
ns.update(COMMIT_SHA=commit,DRIVE_MOUNT=mount,TEAM_DRIVE_DIR=str(team),
          LOCAL_WORK_DIR=work,REPO_DIR=work/'checkout')
real_run=subprocess.run
def local_clone(args,*a,**kw):
    if args[:3]==['git','clone','--no-checkout']:
        assert args[3]=='https://github.com/ChickenDev-cyber/arxiv-data-science.git'
        args=[*args[:3],fixture,*args[4:]]
    return real_run(args,*a,**kw)
try:
    with patch('subprocess.run',side_effect=local_clone):
        exec(compile(cells['code'],'notebook:code','exec'),ns)
    result={'status':'ready','commit':ns['RESOLVED_COMMIT'],'checkout_exists':ns['REPO_DIR'].exists()}
except (AssertionError,ValueError) as exc:
    result={'status':'rejected','reason':str(exc),'checkout_exists':ns['REPO_DIR'].exists()}
print('RESULT_JSON='+json.dumps(result))
'''
        result = subprocess.run([sys.executable, '-c', script,
            str(self.repo/'src/m2_colab_runbook.ipynb'), str(self.fixture),
            str(self.root/name), commit], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return json.loads(next(line.removeprefix('RESULT_JSON=')
                               for line in result.stdout.splitlines() if line.startswith('RESULT_JSON=')))

    def test_default_clones_exact_commit_without_code_zip(self):
        result = self.execute_bootstrap('valid', self.commit)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['commit'], self.commit)
        self.assertTrue(result['checkout_exists'])

    def test_mutable_branch_is_rejected_before_checkout_creation(self):
        result = self.execute_bootstrap('invalid', 'main')
        self.assertEqual(result['status'], 'rejected')
        self.assertFalse(result['checkout_exists'])


if __name__ == '__main__':
    unittest.main()
