"""Handoff consistency and completeness are separate checks."""
from ..contracts import RunScope,SCHEMA_VERSION
from ..runner.scope import contains
from .results import _check,issue,validate_result


def subject(scope,task=None,step="metadata",version=None):
    task=task or {"owner":scope["scope"]["ranges"][0]["owner"] if scope["scope"]["ranges"] else "trong","arxiv_id":None}
    return {**task,"step":step,"version":version}


def validate_handoff(scope,accepted):
    if _check(scope,RunScope,""):return [issue({},None,"Malformed RunScope")]
    errors=[];data=scope["scope"];papers=data["paper_tasks"]
    known={(p["owner"],p["arxiv_id"]) for p in papers}
    def add(r,message):errors.append(issue(r,None,message))
    if scope["schema_version"]!=SCHEMA_VERSION or len(known)!=len(papers) or data["paper_count"]!=len(papers) or data["owner_count"]!=len({p["owner"] for p in papers}):
        add(subject(scope),"Scope identity/counts disagree")
    for p in papers:
        if not any(c["owner"]==p["owner"] and contains(c,p["arxiv_id"]) for c in data["ranges"]):
            add(subject(scope,p),"Scope paper outside assigned range")
    seen=set();metadata={}
    for r in accepted:
        current=validate_result(r)
        if current:errors.extend(current);continue
        key=(r["owner"],r["step"],r["arxiv_id"],r["version"])
        if key in seen:add(r,"Duplicate accepted task")
        seen.add(key)
        if r["run_id"]!=scope["run_id"] or r["is_mock"]!=scope["is_mock"]:add(r,"Accepted result belongs to another run/mode")
        if r["status"] not in ("success","partial","empty","unsupported"):add(r,"Unaccepted outcome in handoff")
        if r["step"]=="discovery":
            if r["owner"] not in {c["owner"] for c in data["ranges"]}:add(r,"Discovery owner outside scope")
        elif (r["owner"],r["arxiv_id"]) not in known:add(r,"Accepted paper outside scope")
        if r["step"]=="metadata":metadata[r["owner"],r["arxiv_id"]]=r
    for r in accepted:
        if not isinstance(r,dict) or r.get("step")!="sources" or validate_result(r):continue
        m=metadata.get((r["owner"],r["arxiv_id"]))
        if m is None or r["version"] not in {v["version"] for v in m["data"]["versions"]}:
            add(r,"Source version was not verified by accepted metadata")
    complete=bool(papers) and all((p["owner"],p["arxiv_id"]) in metadata and metadata[p["owner"],p["arxiv_id"]]["data"]["versions_complete"] for p in papers)
    if data["versions_complete"]!=complete:add(subject(scope),"Scope versions_complete disagrees with metadata")
    return errors


def validate_corpus(scope,accepted):
    errors=validate_handoff(scope,accepted)
    if errors:return errors
    bykey={(r["owner"],r["step"],r["arxiv_id"],r["version"]):r for r in accepted}
    for c in scope["scope"]["ranges"]:
        d=bykey.get((c["owner"],"discovery",None,None))
        if d is None or d["error"] or d["data"]["unresolved_ids"]:
            errors.append(issue(subject(scope,{"owner":c["owner"],"arxiv_id":None},"discovery"),None,"Discovery has unresolved work"))
    for task in scope["scope"]["paper_tasks"]:
        owner,aid=task["owner"],task["arxiv_id"]
        m=bykey.get((owner,"metadata",aid,None))
        if m is None or m["status"]!="success":errors.append(issue(subject(scope,task),None,"Metadata/history is incomplete"))
        for v in m["data"]["versions"] if m else []:
            s=bykey.get((owner,"sources",aid,v["version"]))
            if s is None or s["status"]!="success":errors.append(issue(subject(scope,task,"sources",v["version"]),None,"TeX source unavailable or incomplete"))
        r=bykey.get((owner,"references",aid,None))
        if r is None or r["status"] not in ("success","empty"):errors.append(issue(subject(scope,task,"references"),None,"References/pages or reference metadata incomplete"))
    return errors
