"""Validate source handoff paths and bytes without changing the filesystem."""
from pathlib import Path, PurePosixPath
from ..contracts import paper_folder
from .results import issue


def safe_path(root, relative):
    if not isinstance(relative, str) or "\\" in relative or "\x00" in relative:
        raise ValueError("Invalid relative path")
    parts = relative.split("/")
    if any(p in ("", ".", "..") for p in parts) or ":" in parts[0] or PurePosixPath(relative).is_absolute():
        raise ValueError("Unsafe relative path")
    target = Path(root).absolute()
    if target.is_symlink():
        raise ValueError("Output root cannot be a symlink")
    for part in parts:
        target /= part
        if target.is_symlink():
            raise ValueError("Symlinks are not accepted")
    return target


def validate_source_files(result, context, *, location="staging"):
    if result["step"] != "sources" or result["data"] is None:
        return []
    data, errors = result["data"], []
    expected_dir = f"raw/{result['owner']}/{paper_folder(result['arxiv_id'])}/v{result['version']}"
    try:
        if data["source_dir"] != expected_dir or location not in ("staging", "raw"):
            raise ValueError("Wrong source directory or location")
        relative = expected_dir if location == "raw" else (
            f"staging/{context['run_id']}/{result['owner']}/{paper_folder(result['arxiv_id'])}/v{result['version']}"
        )
        directory = safe_path(context["output_root"], relative)
        expected = {}
        for f in data["files"]:
            path = f["path"]
            safe_path(directory, path)
            if path in expected or Path(path).suffix.lower() not in (".tex", ".bib") or type(f["size_bytes"]) is not int or f["size_bytes"] < 0:
                raise ValueError("Invalid source manifest entry")
            expected[path] = f["size_bytes"]
        actual = {}
        for p in directory.rglob("*"):
            if p.is_symlink():
                raise ValueError("Source handoff contains symlink")
            if p.is_file():
                name = p.relative_to(directory).as_posix()
                if location == "raw" and name == "source_manifest.json":
                    continue
                actual[name] = p.stat().st_size
        if expected != actual:
            raise ValueError("Files or sizes differ from source manifest")
        retained = result["metrics"]["retained_source_bytes"]
        if retained is not None and retained != sum(actual.values()):
            raise ValueError("Retained source byte count disagrees with files")
        if result["status"] in ("success", "partial") and not any(n.lower().endswith(".tex") for n in actual):
            raise ValueError("Accepted source must include TeX")
        if result["status"] == "unsupported" and actual:
            raise ValueError("Unsupported source cannot retain files")
    except (ValueError, OSError) as exc:
        errors.append(issue(result, "/data/files", str(exc)))
    return errors
