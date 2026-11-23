"""Module-E runner: real artifacts now, fixed A–D adapters when delivered."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import time

from .contracts import OWNERS, atomic_json, load_inventory
from .export import build_predictions, check_matching_inputs, export_owner
from .splitting import make_pool, make_split


def _path(base, value):
    if not isinstance(value,str) or not value:
        raise ValueError('Configuration paths must be nonempty strings')
    return (base/value).resolve()


def _adapters(prepare):
    required = {'labeling':['build_labels'],'modeling':['train','score'],'evaluation':['evaluate'],'validation':['validate_dataset']}
    if prepare:
        required['data']=['prepare_inputs']
    result={}
    for module, names in required.items():
        try:
            loaded=importlib.import_module(f'arxiv_pipeline.matching.{module}')
            for name in names:
                function=getattr(loaded,name)
                if not callable(function):
                    raise AttributeError(name)
                result[name]=function
        except (ImportError,AttributeError) as error:
            raise RuntimeError(f'Full mode requires module {module}: {", ".join(names)}. Use artifacts mode for delivered artifacts.') from error
    return result


def _hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def preflight(config: Path):
    """Read-only configuration/module/input check, also usable by the notebook."""
    config=Path(config).resolve()
    cfg=json.loads(config.read_text())
    if cfg.get('mode') not in ('artifacts','full'):
        raise ValueError('mode must be artifacts or full')
    if type(cfg.get('seed',42)) is not int:
        raise ValueError('seed must be an integer')
    owners=cfg.get('owners')
    if not isinstance(owners,list) or not owners or len(set(owners))!=len(owners) or any(o not in OWNERS for o in owners):
        raise ValueError('owners must be a nonempty unique list of known owners')
    required=['inventory','manual_papers','output_dir']
    if cfg['mode']=='artifacts': required+=['labels','scores']
    else: required+=['manual_dir']
    for key in required:
        if key not in cfg:
            raise ValueError(f'Missing configuration key: {key}')
    for key in ['inventory','manual_papers','output_dir','labels','scores','manual_dir','source_config']:
        if key in cfg:
            cfg[key]=_path(config.parent,cfg[key])
    if cfg['output_dir'].exists():
        raise FileExistsError(cfg['output_dir'])
    source=load_inventory(cfg['inventory'])
    if any(o not in source['owners'] for o in owners):
        raise ValueError('Export owner absent from inventory')
    for key in ['manual_papers']+(['labels','scores'] if cfg['mode']=='artifacts' else []):
        if not cfg[key].is_file():
            raise ValueError(f'Missing input file {key}: {cfg[key]}')
    adapters=_adapters('source_config' in cfg) if cfg['mode']=='full' else {}
    return cfg, source, adapters


def run_pipeline(config: Path) -> Path:
    cfg, source, adapters=preflight(config)
    root=cfg['output_dir']
    # Keep read-only input roots outside the run directory.
    inputs=[cfg['inventory'],cfg['manual_papers']]
    inputs += [cfg[k] for k in ('labels','scores','source_config') if k in cfg]
    for info in source['owners'].values():
        inputs += [p for paths in info['tables'].values() for p in paths]
    if any(p==root or root in p.parents for p in inputs):
        raise ValueError('Output directory cannot contain input artifacts')
    root.mkdir(parents=True,exist_ok=False)
    state=dict(schema_version=1,mode=cfg['mode'],seed=cfg.get('seed',42),owners=cfg['owners'],status='running',evaluation_status='not_run',input_hashes={},snapshots={o:i['snapshot'] for o,i in source['owners'].items()})
    started=time.monotonic()
    stage='initialization'
    def step(name, function, *args, **kwargs):
        nonlocal stage
        stage=name
        state['stage']=name
        atomic_json(root/'run_config.json',state)
        value=function(*args,**kwargs)
        return value
    def artifact(value, directory=False):
        path=Path(value).resolve()
        if not (path.is_dir() if directory else path.is_file()):
            raise ValueError(f'Stage {stage} returned absent or wrong-type artifact {path}')
        return path
    try:
        state['input_hashes']={str(p):_hash(p) for p in sorted(set(inputs))}
        try:
            repo=Path(__file__).resolve().parents[3]
            state['code_revision']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True,stderr=subprocess.DEVNULL).strip()
            state['code_dirty']=bool(subprocess.check_output(['git','status','--porcelain'],cwd=repo,text=True))
        except (OSError,subprocess.CalledProcessError):
            state['code_revision']=None
            state['code_dirty']=None
        state['module_e_hashes']={p.name:_hash(p) for p in Path(__file__).parent.glob('*.py')}
        atomic_json(root/'run_config.json',state)
        inventory=cfg['inventory']
        if cfg['mode']=='full' and 'source_config' in cfg:
            inventory=artifact(step('prepare',adapters['prepare_inputs'],cfg['source_config'],root/'inputs'))
            prepared=load_inventory(inventory)
            if prepared['paper_owners']!=source['paper_owners']:
                raise ValueError('Prepared inventory changed configured corpus ownership')
            source=prepared
            state['prepared_inventory']=str(inventory)
            state['snapshots']={o:i['snapshot'] for o,i in source['owners'].items()}
            for info in source['owners'].values():
                for paths in info['tables'].values():
                    for path in paths:
                        state['input_hashes'][str(path)]=_hash(path)
            state['input_hashes'][str(inventory)]=_hash(inventory)
        pool=step('pool',make_pool,inventory,cfg['manual_papers'],root/'pool.parquet',cfg.get('seed',42))
        if cfg['mode']=='full':
            labels=artifact(step('labels',adapters['build_labels'],inventory,pool,cfg['manual_dir'],root/'labels'))
        else:
            labels=cfg['labels']
        state['labels']=str(labels)
        state['labels_sha256']=_hash(labels)
        split=step('split',make_split,pool,labels,root/'split.parquet',cfg.get('seed',42))
        step('matching-inputs',check_matching_inputs,inventory,labels,split)
        methods=['baseline','model'] if cfg['mode']=='full' else ['model']
        model_dir=root/'model'
        if cfg['mode']=='full':
            model_dir=artifact(step('train',adapters['train'],inventory,labels,split,model_dir), directory=True)
        for method in methods:
            if cfg['mode']=='full':
                scores=artifact(step('score-'+method,adapters['score'],inventory,split,model_dir,root/f'scores-{method}.parquet',method))
            else:
                scores=cfg['scores']
            predictions=step('predictions-'+method,build_predictions,inventory,labels,split,scores,root/f'predictions-{method}.parquet')
            if cfg['mode']=='full':
                artifact(step('evaluate-'+method,adapters['evaluate'],inventory,labels,split,scores,predictions,root/'reports'/method))
        if cfg['mode']=='full':
            state['evaluation_status']='completed'
        for owner in cfg['owners']:
            dataset=step('export-'+owner,export_owner,inventory,root/'predictions-model.parquet',owner,root/'export'/owner)
            if cfg['mode']=='full':
                report=artifact(step('validation-'+owner,adapters['validate_dataset'],dataset,inventory,split,root/'reports'/owner/'dataset_validation.json'))
                checked=json.loads(report.read_text())
                if checked.get('valid') is not True:
                    raise ValueError(f'Dataset validation failed for {owner}: {report}')
        state['status']='complete' if cfg['mode']=='full' else 'artifacts_complete'
        state['stage']='done'
    except Exception as error:
        state.update(status='failed',failed_stage=stage,error=f'{type(error).__name__}: {error}')
        raise RuntimeError(f'M3 stage {stage} failed: {error}') from error
    finally:
        state['elapsed_seconds']=time.monotonic()-started
        atomic_json(root/'run_config.json',state)
    return root
