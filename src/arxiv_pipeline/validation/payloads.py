"""Step-specific data semantics; no filesystem writes."""
from ..contracts import DiscoveryData, Metadata, SourcesData, ReferencesData, SourceMetrics, ReferenceMetrics, paper_folder
from ..metadata.api import version_date_order_issues
from ..metadata.service import missing_fields as metadata_missing
from ..references.api import valid_iso_date
from ..identifiers import is_valid_arxiv_id
from .results import _check, issue, pointer


def validate_payload(r):
    step, data, status, metrics = r["step"], r["data"], r["status"], r["metrics"]
    errors = []
    def add(path, message):
        errors.append(issue(r, path, message))
    # A failed lookup may not have observed any metrics. This is not an empty success.
    if data is None and status in ('failed','not_found') and metrics == {}:
        return errors
    hints = {"discovery": DiscoveryData, "metadata": Metadata, "sources": SourcesData, "references": ReferencesData}
    if step in ("discovery", "metadata"):
        if metrics != {}:
            add("/metrics", "This step uses empty metrics")
    else:
        hint = SourceMetrics if step == "sources" else ReferenceMetrics
        for p in _check(metrics, hint, "/metrics"):
            add(p, "Wrong metrics structure")
        if errors:
            return errors
        for key, value in metrics.items():
            if value is not None and value < 0:
                add("/metrics/" + key, "Negative metric")
    if data is None:
        return errors
    for p in _check(data, hints[step], "/data"):
        add(p, "Wrong data structure for step")
    if errors:
        return errors
    if step == "discovery":
        ids = [t["arxiv_id"] for t in data["papers"]]
        unresolved = data["unresolved_ids"]
        excluded = [e["arxiv_id"] for e in data["excluded_ids"]]
        all_ids = ids + unresolved + excluded
        if len(set(all_ids)) != len(all_ids) or not all(is_valid_arxiv_id(i) for i in all_ids):
            add("/data/papers", "Invalid or overlapping discovery IDs")
        if any(t["owner"] != r["owner"] for t in data["papers"]):
            add("/data/papers", "Paper owner differs from discovery")
        if status == "success" and (not data["complete"] or unresolved):
            add("/data/complete", "Discovery success requires full verification")
    elif step == "metadata":
        versions = data["versions"]
        numbers = [v["version"] for v in versions]
        if data["arxiv_id"] != r["arxiv_id"] or numbers != sorted(set(numbers)) or any(n < 1 for n in numbers):
            add("/data/versions", "Invalid metadata identity/version list")
        for field in ("submission_date",):
            if data[field] is not None and not valid_iso_date(data[field]):
                add("/data/" + field, "Invalid ISO date")
        for v in versions:
            if v["submitted_at"] is not None and not valid_iso_date(v["submitted_at"]):
                add("/data/versions", "Invalid version date")
        dates = [v["submitted_at"] for v in versions if v["version"] != 1 and v["submitted_at"] is not None]
        if dates != data["revised_dates"]:
            add("/data/revised_dates", "Revision dates must match known versions")
        first = next((v for v in versions if v["version"] == 1), None)
        if first and first["submitted_at"] != data["submission_date"]:
            # Dates with different precision are compared at the shared precision.
            a, b = first["submitted_at"], data["submission_date"]
            if a is None or b is None or (a[:10] != b[:10] if len(a) == 10 or len(b) == 10 else a != b):
                add("/data/submission_date", "Submission date disagrees with v1")
        missing = metadata_missing(data)
        if data['title'] is not None and not data['title'].strip():
            missing.append('/data/title')
        if data['authors'] is not None and not any(a.strip() for a in data['authors']):
            missing.append('/data/authors')
        if not set(missing) <= set(r["missing_fields"]):
            add("/missing_fields", "Metadata missing fields not declared")
        history_complete = (bool(versions) and numbers == list(range(1,len(versions)+1))
                            and all(v['submitted_at'] is not None and valid_iso_date(v['submitted_at']) for v in versions)
                            and not version_date_order_issues(versions))
        if data["versions_complete"] and not history_complete:
            add("/data/versions_complete", "History is not complete")
        if status == "success" and not (history_complete and not missing and data["versions_complete"]):
            add("/status", "Metadata success requires complete history")
    elif step == "sources":
        expected = f"raw/{r['owner']}/{paper_folder(r['arxiv_id'])}/v{r['version']}"
        if data["source_dir"] != expected:
            add("/data/source_dir", "Wrong source directory")
        if status == "unsupported" and (data["files"] or metrics["retained_source_bytes"] != 0):
            add("/data/files", "Unsupported must retain zero source bytes")
        if status == "success" and (not any(f["path"].lower().endswith(".tex") for f in data["files"]) or any(v is None for v in metrics.values())):
            add("/status", "Sources success needs TeX and complete metrics")
        if all(metrics[k] is not None for k in ("extracted_bytes_before_filter", "retained_source_bytes", "removed_figure_bytes", "removed_other_bytes")):
            if metrics["extracted_bytes_before_filter"] != sum(metrics[k] for k in ("retained_source_bytes", "removed_figure_bytes", "removed_other_bytes")):
                add("/metrics", "Source byte accounting does not balance")
    else:
        refs = data["references"]
        full = 0
        for key, ref in refs.items():
            if not is_valid_arxiv_id(key) or ref["arxiv_id"] != key:
                add("/data/references", "Reference key/ID mismatch")
            known = {"title": bool(ref["title"] and ref["title"].strip()),
                     "authors": bool(ref["authors"] and all(a.strip() for a in ref["authors"])),
                     "submission_date": bool(ref["submission_date"] and valid_iso_date(ref["submission_date"])),
                     "semantic_scholar_id": bool(ref["semantic_scholar_id"] and ref["semantic_scholar_id"].strip())}
            full += all(known.values())
            for field, present in known.items():
                if not present and f"/data/references/{pointer(key)}/{field}" not in r["missing_fields"]:
                    add("/missing_fields", "Reference missing field not declared")
        expected = {"s2_reference_count": len(data["raw_references"]), "arxiv_reference_count": len(refs), "complete_reference_count": full}
        if metrics != expected or full > len(refs) or len(refs) > len(data["raw_references"]):
            add("/metrics", "Reference counts disagree with payload")
        if status == "empty" and (refs or not data["complete"]):
            add("/status", "Empty means a complete arXiv-empty subset")
        if status == "success" and (not refs or full != len(refs) or not data["complete"]):
            add("/status", "References success requires complete metadata/pages")
    return errors
