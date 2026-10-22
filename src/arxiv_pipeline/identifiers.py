"""Shared arXiv ID validation and normalization for metadata and references.

PaperTasks require canonical IDs. References from external APIs may first be
normalized by removing a display prefix, version suffix or old subject class.
These checks validate the ID format, not whether a paper exists on arXiv.
"""

import re

_MODERN_ID = re.compile(r"([0-9]{2})([0-9]{2})\.([0-9]{4,5})")
_OLD_ID = re.compile(r"[a-z]+(?:-[a-z]+)*/([0-9]{2})([0-9]{2})([0-9]{3})")
_OLD_DISPLAY_ID = re.compile(r"([a-z]+(?:-[a-z]+)*)\.[A-Za-z]{2}/([0-9]{7})")
_VERSION_SUFFIX = re.compile(r"v[1-9][0-9]*$")


def is_valid_arxiv_id(value: object) -> bool:
    """Accept canonical IDs only: no whitespace, URL, prefix, class or version.

    Modern IDs use four sequence digits from 0704 through 1412 and five from
    1501. Old archive/YYMMNNN IDs span 9108 through 0703. Months are 01–12;
    sequence numbers start at one. Numeric components use ASCII digits.
    """
    if not isinstance(value, str):
        return False
    if match := _MODERN_ID.fullmatch(value):
        year, month, number = int(match[1]), int(match[2]), match[3]
        digits = 5 if year >= 15 else 4
        return (1 <= month <= 12 and (year, month) >= (7, 4)
                and len(number) == digits and int(number) >= 1)
    if match := _OLD_ID.fullmatch(value):
        year, month, number = int(match[1]), int(match[2]), int(match[3])
        yymm = (year + (1900 if year >= 91 else 2000), month)
        return 1 <= month <= 12 and (1991, 8) <= yymm <= (2007, 3) and number >= 1
    return False


def normalize_arxiv_id(value: object) -> str | None:
    """Normalize an external ID, then apply the shared canonical validation.

    For example, `` arXiv:math.GT/0309136v2 `` becomes ``math/0309136``.
    URLs and invalid IDs return None; malformed dates/digit counts are never
    repaired by guessing or padding.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text[:6].lower() == "arxiv:":
        text = text[6:]
    text = _VERSION_SUFFIX.sub("", text)
    if match := _OLD_DISPLAY_ID.fullmatch(text):
        text = f"{match[1]}/{match[2]}"
    return text if is_valid_arxiv_id(text) else None
