"""
book/lib/huynhnhi_charts.py  (chủ file: Huỳnh Nhi)
--------------------------------------------------
Đọc các CSV nhỏ trong data/huynhnhi/ và trả về DataFrame / hình matplotlib
cho mục 1.3, 1.4, 1.7. Hàm được gọi trong code-cell của file .md nên hình
được SINH KHI BUILD sách, không chèn ảnh tĩnh.

Quy tắc: chỉ import pandas/matplotlib và (nếu cần) book.lib.common;
không import file của người khác.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

DATA = Path(__file__).resolve().parents[2] / "data" / "huynhnhi"


def load(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA / name)


def manifest() -> dict:
    return json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))


def scope_papers() -> int:
    """Tổng số bài trong scope của các run đã đọc."""
    return sum(r["paper_count"] for r in manifest()["runs"])


def mock_banner() -> str:
    """Dòng ghi phạm vi dữ liệu ở đầu trang.

    Mock: cảnh báo số liệu giả. Thật: ghi rõ mốc (run + bản chụp resume) và tiến độ
    từng bước, vì crawl có thể chưa xong ("cào tới đâu thống kê tới đó").
    """
    m = manifest()
    if m["is_mock"]:
        return "**Dữ liệu mẫu (is_mock = true): số liệu chỉ để kiểm tra luồng build.**"
    runs = m["runs"]
    papers = sum(r["paper_count"] for r in runs)
    step = {k: sum(r["accepted_by_step"].get(k, 0) for r in runs)
            for k in ("metadata", "sources", "references")}
    snaps = ", ".join(f"`{r.get('snapshot', r['run_id'])}`" for r in runs)
    return (f"**Phạm vi dữ liệu:** dữ liệu crawl thật, mốc {snaps}. "
            f"Scope gồm {papers} bài; đã chấp nhận metadata của {step['metadata']} bài, "
            f"nguồn TeX của {step['sources']} phiên bản và references của {step['references']} bài. "
            f"Crawl chưa hoàn tất nên số liệu chỉ phản ánh phần đã thu tại mốc này.")


# ------------------------------------------------------------------ mục 1.3
def metadata_status_table(meta: pd.DataFrame) -> pd.DataFrame:
    t = meta["status"].value_counts().rename_axis("status").reset_index(name="số bài")
    t["tỷ lệ (%)"] = (t["số bài"] / t["số bài"].sum() * 100).round(2)
    return t


def fig_versions_hist(meta: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(6, 3.5))
    counts = meta["version_count"].value_counts().sort_index()
    ax.bar(counts.index.astype(str), counts.values)
    ax.set_xlabel("Số phiên bản của bài")
    ax.set_ylabel("Số bài")
    ax.set_title("Phân bố số phiên bản mỗi bài")
    fig.tight_layout()
    return fig


def missing_fields_table(meta: pd.DataFrame) -> pd.DataFrame:
    s = meta["missing_fields"].fillna("").str.split(";").explode()
    s = s[s != ""].str.replace(r"/\d+/", "/*/", regex=True)  # gộp /versions/0/... theo trường
    if s.empty:  # không bài nào thiếu trường: hiện một dòng thay vì bảng rỗng
        return pd.DataFrame({"trường thiếu": ["(không có bài nào thiếu trường)"], "số bài": [0]})
    return s.value_counts().rename_axis("trường thiếu").reset_index(name="số bài")


# ------------------------------------------------------------------ mục 1.4
def fig_source_kind(src: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(6, 3.5))
    counts = src["source_kind"].fillna("(không tải được)").value_counts()
    ax.barh(counts.index, counts.values)
    ax.set_xlabel("Số phiên bản")
    ax.set_title("Phân bố source_kind")
    fig.tight_layout()
    return fig


def fig_size_before_after(src: pd.DataFrame):
    ok = src[src["status"] == "success"]
    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.hist([ok["extracted_bytes_before_filter"] / 1024, ok["retained_source_bytes"] / 1024],
            bins=30, label=["trước lọc", "sau lọc"], log=True)
    ax.set_xlabel("KiB mỗi phiên bản")
    ax.set_ylabel("Số phiên bản (log)")
    ax.legend()
    ax.set_title("Kích thước nguồn trước/sau lọc")
    fig.tight_layout()
    return fig


def removed_bytes_table(src: pd.DataFrame) -> pd.DataFrame:
    ok = src[src["status"] == "success"]
    total = ok["extracted_bytes_before_filter"].sum()
    rows = [("giữ lại (.tex/.bib)", ok["retained_source_bytes"].sum()),
            ("loại do hình", ok["removed_figure_bytes"].sum()),
            ("loại khác", ok["removed_other_bytes"].sum())]
    t = pd.DataFrame(rows, columns=["phần", "bytes"])
    t["tỷ lệ (%)"] = (t["bytes"] / total * 100).round(2) if total else 0
    t["GiB"] = (t["bytes"] / 1024 ** 3).round(2)  # dễ đọc hơn số byte dạng 1.5e+11
    return t[["phần", "GiB", "tỷ lệ (%)"]]


# ------------------------------------------------------------------ mục 1.7
def issues_by_group(issues: pd.DataFrame) -> pd.DataFrame:
    if issues.empty:
        return pd.DataFrame({"report": ["(không có issue)"], "step": [""], "message": [""], "số issue": [0]})
    return (issues.groupby(["report", "step", "message"]).size()
            .reset_index(name="số issue").sort_values("số issue", ascending=False))
