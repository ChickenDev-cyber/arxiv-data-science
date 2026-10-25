"""Explicit sample selection and verified version planning."""
from copy import deepcopy
from ..contracts import SCHEMA_VERSION
from ..identifiers import is_valid_arxiv_id
from .models import paper_spec


def contains(config, arxiv_id):
    if not is_valid_arxiv_id(arxiv_id) or "/" in arxiv_id:
        return False
    month, number = arxiv_id.split(".")
    first = (config["start_month"][2:].replace("-", ""), config["start_id"])
    last = (config["end_month"][2:].replace("-", ""), config["end_id"])
    value = (month, int(number))
    return first <= value <= last if config["inclusive"] else first <= value < last


def initial_scope(options):
    return {"schema_version": SCHEMA_VERSION, "run_id": options.run_id, "is_mock": options.is_mock,
            "scope": {"ranges": options.ranges, "paper_tasks": [], "discovery_complete": False,
                      "versions_complete": False, "owner_count": 0, "paper_count": 0}}


def select_papers(discovery, options):
    if discovery["data"] is None:
        return []
    papers = discovery["data"]["papers"]
    config = next(c for c in options.ranges if c["owner"] == discovery["owner"])
    seen = set()
    for task in papers:
        if task["owner"] != config["owner"] or not contains(config, task["arxiv_id"]) or task["arxiv_id"] in seen:
            raise ValueError("Discovery returned duplicate or out-of-range paper")
        seen.add(task["arxiv_id"])
    return deepcopy(papers[:options.max_papers])


def source_specs(task, metadata):
    return [paper_spec(task, "sources", v["version"]) for v in
            (metadata["data"]["versions"] if metadata["data"] else [])]


def update_scope(scope, selected, accepted):
    wrapper = deepcopy(scope)
    data = wrapper["scope"]
    tasks = {(t["owner"], t["arxiv_id"]): t for t in data["paper_tasks"]}
    for t in selected:
        if not any(c["owner"] == t["owner"] and contains(c, t["arxiv_id"]) for c in data["ranges"]):
            raise ValueError("Selected paper outside assigned scope")
        tasks[t["owner"], t["arxiv_id"]] = dict(t)
    data["paper_tasks"] = [tasks[k] for k in sorted(tasks)]
    data["paper_count"] = len(tasks)
    data["owner_count"] = len({t[0] for t in tasks})
    metadata = {(r["owner"], r["arxiv_id"]): r for r in accepted if r["step"] == "metadata"}
    data["versions_complete"] = bool(tasks) and all(
        key in metadata and metadata[key]["data"]["versions_complete"] for key in tasks)
    return wrapper
