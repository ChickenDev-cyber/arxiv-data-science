"""Synthetic T4 handoff examples, produced by the real module with offline fake readers.

    PYTHONPATH=src python -m arxiv_pipeline.references.examples --owner man-nhi --output-dir /tmp/arxiv-t4-examples

Every record is invented (is_mock=true, no network). Never use them as real data.
"""

import argparse
import json
from pathlib import Path

from ..config import OWNERS
from ..discovery.api import RemoteError
from .service import ReferencesModule


class FakeReader:
    """Replays page payloads (dict) or errors (RemoteError) in order."""

    def __init__(self, replies):
        self.replies, self.calls, self.attempts = list(replies), [], 0

    def references_page(self, arxiv_id, offset, limit):
        self.calls.append((arxiv_id, offset, limit))
        self.attempts += 1
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):  # also lets tests simulate Ctrl+C
            raise reply
        return reply


class FakeDates:
    """Returns the configured submission dates for the IDs it knows."""

    def __init__(self, known=None, error=None):
        self.known, self.error, self.calls, self.attempts = dict(known or {}), error, [], 0

    def submission_dates(self, ids):
        self.calls.append(list(ids))
        self.attempts += 1
        if self.error:
            raise self.error
        return {i: self.known[i] for i in ids if i in self.known}


def cited(arxiv_id, title="SYNTHETIC reference", authors=("Example Author",), paper_id=None):
    return {"citedPaper": {
        "paperId": paper_id if paper_id is not None else f"synthetic-s2-{arxiv_id}",
        "title": title, "authors": [{"authorId": None, "name": n} for n in authors],
        "externalIds": {"ArXiv": arxiv_id} if arxiv_id else {"DOI": "10.0000/synthetic"}}}


def page(items, next_offset=None):
    payload = {"offset": 0, "data": items}
    if next_offset is not None:
        payload["next"] = next_offset
    return payload


def scenarios():
    """name -> (reader replies, date reader kwargs)."""
    ok_dates = {"2001.00001": "2020-01-01T00:00:00Z", "2001.00002": "2020-01-02T00:00:00Z"}
    return {
        "success": ([page([cited("2001.00001"), cited("2001.00002"), cited(None)])],
                    dict(known=ok_dates)),
        "missing_date": ([page([cited("2001.00001"), cited("2001.00002")])],
                         dict(known={"2001.00001": "2020-01-01T00:00:00Z"})),
        "empty": ([page([cited(None)])], {}),
        "page_error": ([page([cited("2001.00001")], next_offset=1),
                        RemoteError("rate_limited", "Synthetic HTTP 429", True, 429)],
                       dict(known=ok_dates)),
    }


def build(name, owner, arxiv_id="2501.12642"):
    replies, date_kwargs = scenarios()[name]
    module = ReferencesModule(FakeReader(replies), FakeDates(**date_kwargs), page_size=1)
    context = {"run_id": "mock-demo", "is_mock": True, "output_root": Path("/tmp/arxiv-t4-examples")}
    return module.fetch_references({"owner": owner, "arxiv_id": arxiv_id}, context)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, choices=sorted(OWNERS),
                        help="owner written into the synthetic Results")
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/arxiv-t4-examples"))
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise SystemExit("Output directory must be empty; choose a new path.")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name in scenarios():
        text = json.dumps(build(name, args.owner), ensure_ascii=False, indent=2) + "\n"
        (args.output_dir / f"references.{name}.result.json").write_text(text, encoding="utf-8")
    print(f"Wrote {len(scenarios())} synthetic T4 results to {args.output_dir}")


if __name__ == "__main__":
    main()
