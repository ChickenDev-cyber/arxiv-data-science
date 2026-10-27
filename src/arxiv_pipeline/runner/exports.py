"""Durable Result events and consistent stopped-run handoff files."""
import os
from pathlib import Path
import tempfile
from ..validation.artifacts import safe_path
from ..validation.corpus import validate_handoff,validate_corpus
from ..validation.results import issue
from .models import canonical
from .writer import atomic_json,sync_dir


def atomic_lines(path,values):
    path.parent.mkdir(parents=True,exist_ok=True);temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,delete=False,prefix=".t5-") as stream:
            temporary=Path(stream.name)
            for value in values:stream.write(canonical(value)+"\n")
            stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path);sync_dir(path.parent)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


class Exporter:
    def __init__(self,context,store,publisher):
        self.context,self.store,self.publisher=context,store,publisher
        self.root=Path(context["output_root"]);self.run_id=context["run_id"]
        self.validation={"valid":True,"issues":[]};self.quality={"valid":True,"issues":[]}
        self.extra_issues=[]

    def mark_unavailable(self):
        r={'owner':self.store.read_run_record().config['owners'][0],
           'arxiv_id':None,'version':None,'step':'discovery'}
        atomic_json(safe_path(self.root,f'runs/{self.run_id}/validation.json'),{
            'valid':False,'issues':[issue(r,None,'Handoff is being updated; wait for a successful checkpoint')]})

    def rebuild_events(self):
        atomic_lines(safe_path(self.root,f"logs/{self.run_id}/events.jsonl"),self.store.iter_results(accepted_only=False))

    def append_event(self,result):
        path=safe_path(self.root,f"logs/{self.run_id}/events.jsonl");path.parent.mkdir(parents=True,exist_ok=True)
        with path.open("a",encoding="utf-8") as stream:
            stream.write(canonical(result)+"\n");stream.flush();os.fsync(stream.fileno())

    def checkpoint(self):
        # Invalidate the previous readiness marker before either handoff file can change.
        self.mark_unavailable()
        errors=list(self.extra_issues)
        for row in self.store.tasks():
            issues=self.publisher.verify_task(row["task_key"])
            if issues:self.store.invalidate(row["task_key"]);errors.extend(issues)
        con=self.store.connection;con.execute("BEGIN")
        try:
            scope=self.store.read_scope();accepted=list(self.store.iter_results(accepted_only=True))
            errors+=validate_handoff(scope,accepted)
            self.validation={"valid":not errors,"issues":errors}
            quality=validate_corpus(scope,accepted)+self.publisher.issues
            self.quality={"valid":not quality,"issues":quality}
            base=f"runs/{self.run_id}"
            atomic_json(safe_path(self.root,base+"/quality.json"),self.quality)
            if not errors:
                atomic_json(safe_path(self.root,base+"/scope.json"),scope)
                atomic_lines(safe_path(self.root,base+"/accepted_results.jsonl"),accepted)
            atomic_json(safe_path(self.root,base+"/validation.json"),self.validation)
            con.commit()
        except BaseException:
            con.rollback();raise
