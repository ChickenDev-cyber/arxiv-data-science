import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow.parquet as pq

from tests.matching.fixtures import Corpus
from arxiv_pipeline.matching.contracts import write_rows
from arxiv_pipeline.matching.splitting import make_pool
from arxiv_pipeline.matching.pipeline import run_pipeline


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.c = Corpus(self.root)
        self.pool = make_pool(self.c.inventory,self.c.manual,self.root/'expected-pool.parquet')
        self.labels = self.c.labels(self.pool)
        # Scoring covers the pool regardless of partition.
        self.scores = self.c.scores(self.pool)
        self.config = self.root/'config.json'
        self.cfg = dict(mode='artifacts',inventory=str(self.c.inventory),manual_papers=str(self.c.manual),labels=str(self.labels),scores=str(self.scores),output_dir=str(self.root/'run'),owners=['trong'],seed=42)

    def run_config(self):
        self.config.write_text(json.dumps(self.cfg))
        return run_pipeline(self.config)

    def test_artifact_run_writes_truthful_status_and_personal_export(self):
        out = self.run_config()
        state = json.loads((out/'run_config.json').read_text())
        self.assertEqual(state['evaluation_status'],'not_run')
        self.assertEqual(state['status'],'artifacts_complete')
        self.assertTrue(state['input_hashes'])
        self.assertTrue((out/'export/trong/data/predictions/part-00000.parquet').is_file())
        self.assertEqual(pq.read_table(out/'predictions-model.parquet').num_rows,140)
        with self.assertRaises(FileExistsError): run_pipeline(self.config)

    def test_missing_modules_and_bad_config_are_preflight_errors(self):
        self.cfg['mode']='full'
        self.cfg['manual_dir']=str(self.root/'manual')
        with self.assertRaisesRegex(RuntimeError, r'Full mode requires module'):
            self.run_config()
        self.assertFalse((self.root/'run').exists())
        self.cfg['mode']='unknown'
        with self.assertRaises(ValueError): self.run_config()
        self.assertFalse((self.root/'run').exists())

    def test_stage_error_is_recorded_and_does_not_publish_predictions(self):
        rows=pq.read_table(self.scores).to_pylist()
        write_rows(self.root/'missing-score.parquet','scores',rows[:-1])
        self.cfg['scores']=str(self.root/'missing-score.parquet')
        with self.assertRaisesRegex(RuntimeError,'predictions'):
            self.run_config()
        state=json.loads((self.root/'run/run_config.json').read_text())
        self.assertEqual(state['status'],'failed')
        self.assertEqual(state['failed_stage'],'predictions-model')
        self.assertFalse((self.root/'run/predictions-model.parquet').exists())

    def adapters(self, valid=True):
        modules={}
        for name in ['labeling','modeling','evaluation','validation','data']:
            m=types.ModuleType('arxiv_pipeline.matching.'+name)
            modules[m.__name__]=m
        def prepare(source_config, output_dir):
            self.assertEqual(source_config,self.c.inventory)
            return self.c.inventory
        def labels(inventory,pool,manual_dir,output_dir):
            self.assertEqual(inventory,self.c.inventory)
            return self.labels
        def train(inventory,labels,split,model_dir):
            self.assertTrue(Path(split).is_file())
            model_dir.mkdir()
            (model_dir/'fixture.txt').write_text('adapter boundary only: not a trained model')
            return model_dir
        def score(inventory,split,model_dir,output_path,method):
            rows=pq.read_table(self.scores).to_pylist()
            if method=='model':
                for row in rows: row['score']=float(row['ref_arxiv_id']=='2401.00020')
            return write_rows(output_path,'scores',rows)
        def evaluate(inventory,labels,split,scores,predictions,report_dir):
            self.assertTrue(Path(predictions).is_file())
            report_dir.mkdir(parents=True)
            out=report_dir/'metrics.json'
            out.write_text(json.dumps({'fixture_adapter':True,'n':140}))
            return out
        def validate(dataset_dir,inventory,split,output_path):
            self.assertTrue((dataset_dir/'README.md').is_file())
            output_path.parent.mkdir(parents=True,exist_ok=True)
            output_path.write_text(json.dumps({'valid':valid}))
            return output_path
        modules['arxiv_pipeline.matching.data'].prepare_inputs=prepare
        modules['arxiv_pipeline.matching.labeling'].build_labels=labels
        modules['arxiv_pipeline.matching.modeling'].train=train
        modules['arxiv_pipeline.matching.modeling'].score=score
        modules['arxiv_pipeline.matching.evaluation'].evaluate=evaluate
        modules['arxiv_pipeline.matching.validation'].validate_dataset=validate
        return modules

    def test_full_orchestration_calls_fixed_adapters_and_exports_model(self):
        self.cfg.update(mode='full',manual_dir=str(self.root/'manual'),source_config=str(self.c.inventory))
        with patch.dict(sys.modules,self.adapters()): out=self.run_config()
        for method in ['baseline','model']:
            self.assertTrue((out/f'predictions-{method}.parquet').is_file())
            self.assertTrue((out/f'reports/{method}/metrics.json').is_file())
        rows=pq.read_table(out/'export/trong/data/predictions').to_pylist()
        self.assertTrue(all(r['cand_1']=='2401.00020' for r in rows))
        state=json.loads((out/'run_config.json').read_text())
        self.assertEqual(state['status'],'complete')
        self.assertEqual(state['evaluation_status'],'completed')

    def test_false_validation_never_marks_full_run_complete(self):
        self.cfg.update(mode='full',manual_dir=str(self.root/'manual'))
        with patch.dict(sys.modules,self.adapters(valid=False)):
            with self.assertRaisesRegex(RuntimeError,'validation'):
                self.run_config()
        state=json.loads((self.root/'run/run_config.json').read_text())
        self.assertEqual(state['status'],'failed')

    def test_provenance_read_error_records_failure(self):
        with patch('arxiv_pipeline.matching.pipeline._hash', side_effect=OSError('source disappeared')):
            with self.assertRaises(RuntimeError): self.run_config()
        state=json.loads((self.root/'run/run_config.json').read_text())
        self.assertEqual(state['status'],'failed')
        self.assertEqual(state['failed_stage'],'initialization')

    def test_evaluator_returning_directory_does_not_count_as_metric(self):
        self.cfg.update(mode='full',manual_dir=str(self.root/'manual'))
        modules=self.adapters()
        def bad_evaluate(*args):
            directory=args[-1]
            directory.mkdir(parents=True)
            return directory
        modules['arxiv_pipeline.matching.evaluation'].evaluate=bad_evaluate
        with patch.dict(sys.modules,modules):
            with self.assertRaises(RuntimeError): self.run_config()
        state=json.loads((self.root/'run/run_config.json').read_text())
        self.assertEqual(state['status'],'failed')

    def test_cli_can_run_artifacts_without_other_modules(self):
        self.config.write_text(json.dumps(self.cfg))
        command=[sys.executable,'-m','arxiv_pipeline.matching','--config',str(self.config)]
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertTrue((self.root/'run/export/trong/README.md').exists())

if __name__=='__main__': unittest.main()
