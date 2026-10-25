"""Publish validated data with a recoverable filesystem/SQLite journal."""
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid
from ..contracts import paper_folder
from ..validation import validate_result
from ..validation.artifacts import safe_path, validate_source_files
from ..validation.results import issue
from .models import Artifact, canonical


def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda:stream.read(65536),b""):
            h.update(chunk)
    return h.hexdigest()


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY | os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=path.parent,delete=False,prefix=".t5-") as stream:
            temporary=Path(stream.name)
            stream.write(canonical(value)+"\n");stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path);sync_dir(path.parent)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def profile(path):
    if path.is_symlink():raise ValueError("Symlink in publication")
    if not path.exists():return None
    if path.is_file():return {"kind":"file","files":{"":{"size_bytes":path.stat().st_size,"sha256":digest(path)}}}
    files={}
    for p in path.rglob("*"):
        if p.is_symlink():raise ValueError("Symlink in publication tree")
        if p.is_file():files[p.relative_to(path).as_posix()]={"size_bytes":p.stat().st_size,"sha256":digest(p)}
    return {"kind":"directory","files":files}


def _remove(path):
    if path.is_dir():shutil.rmtree(path)
    else:path.unlink(missing_ok=True)


def _preserves(old,new):
    if old is None or old == "":return True
    if isinstance(old,dict):
        return isinstance(new,dict) and all(k in new and _preserves(v,new[k]) for k,v in old.items())
    if isinstance(old,list):
        if not isinstance(new,list):return False
        if old and isinstance(old[0],dict) and "version" in old[0]:
            indexed={v["version"]:v for v in new}
            return all(v["version"] in indexed and _preserves(v,indexed[v["version"]]) for v in old)
        return not (Counter(canonical(v) for v in old)-Counter(canonical(v) for v in new))
    return new is not None and (type(new) is type(old)) and (not isinstance(old,bool) or not old or new)


class Publisher:
    def __init__(self,context,store):
        self.context,self.store=context,store
        self.root=Path(context["output_root"])
        self.issues=[]

    def _payloads(self,r):
        step=r["step"];base=f"raw/{r['owner']}/{paper_folder(r['arxiv_id'])}" if step!="discovery" else ""
        if step=="discovery":return {f"discovery/{r['owner']}.json":r}
        if step=="metadata":return {base+"/metadata.json":r["data"]}
        if step=="references":return {base+"/references.json":r["data"]["references"],base+"/raw_references.json":r["data"]["raw_references"]}
        return {r["data"]["source_dir"]+"/source_manifest.json":r}

    def _targets(self,r):
        return [r["data"]["source_dir"]] if r["step"]=="sources" else list(self._payloads(r))

    def _key(self,r):
        from .models import paper_spec,discovery_spec
        if r["step"]=="discovery":
            config=next(c for c in self.store.read_scope()["scope"]["ranges"] if c["owner"]==r["owner"])
            return discovery_spec(config).key
        return paper_spec({"owner":r["owner"],"arxiv_id":r["arxiv_id"]},r["step"],r["version"]).key

    def _prefer(self,old,new):
        if old is None:return True
        if new["step"]=="metadata":
            before={v["version"] for v in old["data"]["versions"]}
            after={v["version"] for v in new["data"]["versions"]}
            if not before<=after:raise ValueError("Metadata regressed a verified version history")
        if old["status"] in ("success","empty") and new["status"] not in ("success","empty"):
            return False
        if new["step"]=="sources":return new["status"]=="success" or old["status"]!="success"
        before,after=old["data"],new["data"]
        if new["step"]=="discovery":
            before={"papers":before["papers"],"excluded_ids":before["excluded_ids"]}
        if not _preserves(before,after):
            self.issues.append(issue(new,None,"Candidate loses known information; previous result retained"))
            return False
        return True

    def publish(self,attempt_id,r):
        self.issues=[]
        row=self.store.get_task(self._key(r))
        old=row["accepted_result"]
        if r["status"] in ("failed","not_found") or not self._prefer(old,r):
            self.store.complete(attempt_id,False);return False
        if r["step"]=="sources":
            errors=validate_source_files(r,self.context)
            if errors:raise ValueError(errors[0]["message"])
        transaction=uuid.uuid4().hex
        tx=safe_path(self.root,f".transactions/{transaction}")
        operations=[];artifacts=[]
        payloads=self._payloads(r)
        for i,target in enumerate(self._targets(r)):
            relative_new=f".transactions/{transaction}/new/{i}"
            new=safe_path(self.root,relative_new)
            new.parent.mkdir(parents=True,exist_ok=True)
            if r["step"]=="sources":
                stage=safe_path(self.root,f"staging/{r['run_id']}/{r['owner']}/{paper_folder(r['arxiv_id'])}/v{r['version']}")
                if stage.exists():shutil.copytree(stage,new)
                else:new.mkdir()
                atomic_json(new/"source_manifest.json",r)
                for p in new.rglob("*"):
                    if p.is_file():
                        with p.open("rb") as stream:os.fsync(stream.fileno())
            else:atomic_json(new,payloads[target])
            new_profile=profile(new)
            for name,values in new_profile["files"].items():
                artifacts.append(asdict(Artifact(target+("/"+name if name else ""),**values)))
            operations.append({"target":target,"new":relative_new,"backup":f".transactions/{transaction}/backup/{i}",
                               "old":profile(safe_path(self.root,target)),"expected":new_profile})
        manifest={"transaction":transaction,"operations":operations,"artifacts":artifacts}
        self.store.prepare(attempt_id,manifest)
        for op in operations:
            target=safe_path(self.root,op["target"]);new=safe_path(self.root,op["new"]);backup=safe_path(self.root,op["backup"])
            target.parent.mkdir(parents=True,exist_ok=True);backup.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():os.replace(target,backup);sync_dir(target.parent);sync_dir(backup.parent)
            os.replace(new,target);sync_dir(target.parent)
        if not self._installed(manifest):raise ValueError("Publication verification failed")
        self.store.complete(attempt_id,True)
        self._cleanup(manifest)
        self._clean_stage(r)
        return True

    def _installed(self,m):
        return all(profile(safe_path(self.root,op["target"]))==op["expected"] for op in m["operations"])

    def _clean_stage(self,r):
        if r["step"]=="sources":
            stage=safe_path(self.root,f"staging/{r['run_id']}/{r['owner']}/{paper_folder(r['arxiv_id'])}/v{r['version']}")
            if stage.exists():shutil.rmtree(stage)

    def _cleanup(self,m):
        tx=safe_path(self.root,f".transactions/{m['transaction']}")
        if tx.exists():shutil.rmtree(tx)

    def _validate_journal(self,m,r):
        if not re.fullmatch(r"[a-f0-9]{32}",m.get("transaction","")) or validate_result(r):
            raise ValueError("Invalid publication journal")
        if [op.get("target") for op in m.get("operations",[])]!=self._targets(r):
            raise ValueError("Publication journal targets disagree with Result")
        for i,op in enumerate(m["operations"]):
            for field,folder in (("new","new"),("backup","backup")):
                if op.get(field)!=f".transactions/{m['transaction']}/{folder}/{i}":raise ValueError("Unsafe journal scratch path")
                safe_path(self.root,op[field])
            safe_path(self.root,op["target"])

    def recover(self):
        records=self.store.publication_records()
        owned=set()
        for row in records:
            m=json.loads(row["manifest_json"]);r=json.loads(row["result_json"])
            self._validate_journal(m,r);owned.add(m["transaction"])
            if row["state"]=="prepared":
                if self._installed(m):self.store.complete(row["attempt_id"],True)
                else:
                    for op in reversed(m["operations"]):
                        target=safe_path(self.root,op["target"]);backup=safe_path(self.root,op["backup"])
                        actual=profile(target)
                        if actual is not None:
                            if actual==op["old"] and not backup.exists():pass
                            elif actual==op["expected"]:_remove(target)
                            elif actual!=op["old"]:raise ValueError("Unknown file found during rollback")
                        if backup.exists():
                            if profile(backup)!=op["old"]:raise ValueError("Backup checksum mismatch")
                            if target.exists():
                                if profile(target)!=op["old"]:raise ValueError("Rollback target mismatch")
                                _remove(backup)
                            else:target.parent.mkdir(parents=True,exist_ok=True);os.replace(backup,target)
                        if profile(target)!=op["old"]:raise ValueError("Could not restore previous publication")
                    self.store.abort_publication(row["attempt_id"])
            self._cleanup(m)
            self._clean_stage(r)
        directory=safe_path(self.root,".transactions")
        if directory.exists():
            for orphan in directory.iterdir():
                if orphan.is_symlink() or not re.fullmatch(r"[a-f0-9]{32}",orphan.name):raise ValueError("Unknown transaction directory")
                if orphan.name not in owned:shutil.rmtree(orphan)

    def verify_task(self,key):
        row=self.store.get_task(key)
        if not row or row["accepted_result"] is None:return []
        r=row["accepted_result"];errors=validate_result(r)
        try:
            if not row["artifacts"]:raise ValueError("Accepted result has no artifact checksums")
            for a in row["artifacts"]:
                p=safe_path(self.root,a.path)
                if not p.is_file() or p.stat().st_size!=a.size_bytes or digest(p)!=a.sha256:
                    raise ValueError("Accepted artifact missing or changed: "+a.path)
            for path,payload in self._payloads(r).items():
                if json.loads(safe_path(self.root,path).read_text(encoding="utf-8"))!=payload:
                    raise ValueError("Published JSON disagrees with accepted Result")
            if r["step"]=="sources":errors+=validate_source_files(r,self.context,location="raw")
        except (OSError,ValueError) as exc:errors.append(issue(r,None,str(exc)))
        return errors
