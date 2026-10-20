"""Assigned ID ranges for the five team members."""

from .contracts import RangeConfig

ASSIGNED_RANGES: list[RangeConfig] = [
    {
        "owner": "khang",
        "start_month": "2025-06",
        "start_id": 20788,
        "end_month": "2025-07",
        "end_id": 1658,
        "inclusive": True,
    },
    {
        "owner": "trong",
        "start_month": "2025-04",
        "start_id": 17523,
        "end_month": "2025-05",
        "end_id": 665,
        "inclusive": True,
    },
    {
        "owner": "hoang",
        "start_month": "2025-03",
        "start_id": 6914,
        "end_month": "2025-03",
        "end_id": 11913,
        "inclusive": True,
    },
    {
        "owner": "man-nhi",
        "start_month": "2025-01",
        "start_id": 12642,
        "end_month": "2025-01",
        "end_id": 17641,
        "inclusive": True,
    },
    {
        "owner": "huynh-nhi",
        "start_month": "2024-09",
        "start_id": 3594,
        "end_month": "2024-09",
        "end_id": 8593,
        "inclusive": True,
    },
]

OWNERS = {item["owner"] for item in ASSIGNED_RANGES}
