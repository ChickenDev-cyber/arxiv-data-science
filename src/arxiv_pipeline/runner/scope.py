"""Explicit sample selection and verified version planning."""
from copy import deepcopy
from collections import Counter
from itertools import islice
from ..contracts import SCHEMA_VERSION
from ..identifiers import is_valid_arxiv_id
from .models import canonical, paper_spec
from ..discovery.service import candidate_count, candidate_ids


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


def validate_scope_configuration(scope, options) -> None:
    """Bind persisted scope to the exact run configuration, not just to itself."""
    if (not isinstance(scope, dict) or scope.get("run_id") != options.run_id
            or scope.get("schema_version") != SCHEMA_VERSION
            or type(scope.get("is_mock")) is not bool or scope["is_mock"] != options.is_mock):
        raise ValueError("Scope identity differs from the saved run configuration")
    data = scope.get("scope")
    if not isinstance(data, dict) or canonical(data.get("ranges")) != canonical(options.ranges):
        raise ValueError("Scope ranges differ from the saved run configuration")
    papers = data.get("paper_tasks")
    if not isinstance(papers, list):
        raise ValueError("Invalid scope paper list")
    counts = Counter()
    seen = set()
    candidates = ({c["owner"]: set(islice(candidate_ids(c), options.max_candidates)) for c in options.ranges}
                  if options.max_candidates is not None else None)
    for paper in papers:
        if (not isinstance(paper, dict) or set(paper) != {"owner", "arxiv_id"}
                or paper["owner"] not in options.owners
                or not any(c["owner"] == paper["owner"] and contains(c, paper["arxiv_id"])
                           for c in options.ranges)):
            raise ValueError("Scope contains a paper outside the saved configuration")
        identity = paper["owner"], paper["arxiv_id"]
        if identity in seen:
            raise ValueError("Scope contains a duplicate paper")
        seen.add(identity)
        counts[paper["owner"]] += 1
        if candidates is not None and paper["arxiv_id"] not in candidates[paper["owner"]]:
            raise ValueError("Scope contains a paper beyond the saved candidate limit")
    if options.max_papers is not None and any(count > options.max_papers for count in counts.values()):
        raise ValueError("Scope exceeds the saved paper limit")
    for field, expected in (("paper_count", len(papers)), ("owner_count", len(counts))):
        if type(data.get(field)) is not int or data[field] != expected:
            raise ValueError("Scope counts disagree with selected papers")
    if any(type(data.get(flag)) is not bool for flag in ("discovery_complete", "versions_complete")):
        raise ValueError("Invalid scope completeness flags")
    if (data["discovery_complete"] and options.max_candidates is not None
            and any(options.max_candidates < candidate_count(c) for c in options.ranges)):
        raise ValueError("Scope claims complete discovery despite the saved candidate limit")


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
