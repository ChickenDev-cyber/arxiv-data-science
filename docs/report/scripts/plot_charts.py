#!/usr/bin/env python3
"""Generate publication-grade editorial Matplotlib charts for the arXiv DS report.

Palette matches the diagram-design editorial light theme:
  paper  #f5f5f5 | canvas #ffffff | ink #2d3142 | muted #4f5d75 | soft #5a6580
  accent #bf4520 | link   #2e5aa8 | rule #d6dae2
"""

from pathlib import Path
import numpy as np
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

OUT_DIR = Path("/home/phuckhang/MyWorkspace/arxiv-data-science/docs/report/images")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Editorial palette tokens
PAPER = "#f5f5f5"
CANVAS = "#ffffff"
INK = "#2d3142"
MUTED = "#4f5d75"
SOFT = "#5a6580"
RULE = "#d6dae2"
ACCENT = "#bf4520"
ACCENT_SOFT = "#f4dfd8"
LINK = "#2e5aa8"
LINK_SOFT = "#dfe8f6"
SLATE_LIGHT = "#c6cdd8"
SLATE_MED = "#7a869a"

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Noto Sans", "Fira Sans", "DejaVu Sans"],
    "axes.edgecolor": RULE,
    "axes.linewidth": 0.9,
    "axes.facecolor": CANVAS,
    "figure.facecolor": PAPER,
    "text.color": INK,
    "axes.labelcolor": MUTED,
    "xtick.color": MUTED,
    "ytick.color": INK,
    "grid.color": "#e5e8ee",
    "grid.linewidth": 0.7,
    "savefig.facecolor": PAPER,
    "savefig.edgecolor": "none",
})


def style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(RULE)
    ax.spines["bottom"].set_color(RULE)
    ax.tick_params(axis="both", length=0, pad=7)
    ax.set_axisbelow(True)


def build_m1_m2_stats():
    ranges = [
        "2409 · Huỳnh Nhi",
        "2501 · Mẫn Nhi",
        "2503 · Huy Hoàng",
        "2504–05 · Minh Trọng",
        "2506–07 · Phúc Khang",
    ]
    # Exact verified values from report_brief.md (Sections 4.3 & 4.4)
    raw_mib = np.array([4.75, 5.01, 5.63, 4.48, 3.47])
    ret_kib = np.array([290.5, 337.3, 371.7, 265.3, 207.7])
    ret_mib = ret_kib / 1024.0
    m1_drop = ["-94.0%", "-93.4%", "-93.6%", "-94.2%", "-94.2%"]

    before_m = np.array([3.194213, 3.276789, 3.262182, 3.334790, 3.155248])
    after_m = np.array([2.167101, 2.221136, 2.198596, 2.288312, 2.223044])
    m2_drop = ["-32.2%", "-32.2%", "-32.6%", "-31.4%", "-29.5%"]

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(11.8, 4.85), dpi=300,
        gridspec_kw={"width_ratios": [1.08, 1.0], "wspace": 0.25},
    )
    fig.subplots_adjust(left=0.16, right=0.975, top=0.835, bottom=0.195)

    y = np.arange(len(ranges))[::-1]
    h = 0.34

    # --- Panel A: M1 Per-Version Source Size Compression ---
    style_axes(ax1)
    ax1.xaxis.grid(True, linestyle="-", alpha=0.9)

    ax1.barh(
        y + h / 2, raw_mib, height=h, color=SLATE_LIGHT, edgecolor=MUTED,
        linewidth=0.7, label="Raw Extracted (MiB / ver)", zorder=3,
    )
    ax1.barh(
        y - h / 2, ret_mib, height=h, color=ACCENT, edgecolor=ACCENT,
        linewidth=0.7, label="Retained TeX/BibTeX (MiB / ver)", zorder=3,
    )

    ax1.axvline(4.70, color=MUTED, linestyle="--", linewidth=0.9, alpha=0.65, zorder=2)
    ax1.text(
        4.78, -0.58, "Weighted mean raw: 4.70 MiB", fontsize=7.5, color=MUTED,
        va="bottom", ha="left", style="italic",
        bbox=dict(facecolor=CANVAS, edgecolor="none", pad=1.0), zorder=4,
    )

    for yi, raw_v, kib_v, ret_v, pct in zip(y, raw_mib, ret_kib, ret_mib, m1_drop):
        ax1.text(
            raw_v + 0.11, yi + h / 2, f"{raw_v:.2f} MiB",
            va="center", ha="left", fontsize=7.9, color=INK, fontweight="medium",
            bbox=dict(facecolor=CANVAS, edgecolor="none", pad=0.8), zorder=4,
        )
        ax1.text(
            ret_v + 0.11, yi - h / 2, f"{kib_v:.1f} KiB",
            va="center", ha="left", fontsize=7.9, color=ACCENT, fontweight="semibold",
            zorder=4,
        )
        ax1.text(
            7.55, yi, pct,
            va="center", ha="center", fontsize=7.9, color=ACCENT, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.26,rounding_size=0.18",
                      facecolor=ACCENT_SOFT, edgecolor=ACCENT, linewidth=0.65),
            zorder=4,
        )

    ax1.set_yticks(y)
    ax1.set_yticklabels(ranges, fontsize=8.6, fontweight="medium", color=INK)
    ax1.set_xlim(0, 8.35)
    ax1.set_ylim(-0.75, 4.65)
    ax1.set_xticks([0, 1, 2, 3, 4, 5, 6])
    ax1.set_xlabel("Mean Size per Version (MiB)", fontsize=8.7, labelpad=7, color=MUTED)
    ax1.set_title(
        "A. Milestone 1: Extraction-Time Source Filtering",
        loc="left", fontsize=10.5, fontweight="bold", color=INK, pad=20,
    )
    ax1.text(
        0.0, 1.032,
        "35,212 sampled versions · weighted mean 4.70 MiB to 296.7 KiB (-93.8%)",
        transform=ax1.transAxes, fontsize=7.9, color=SOFT, ha="left", va="bottom",
    )
    ax1.legend(
        loc="upper center", bbox_to_anchor=(0.46, -0.185), ncol=2,
        frameon=False, fontsize=8.0, handlelength=1.2, columnspacing=1.1,
    )

    # --- Panel B: M2 Cross-Version Element Deduplication ---
    style_axes(ax2)
    ax2.xaxis.grid(True, linestyle="-", alpha=0.9)

    ax2.barh(
        y + h / 2, before_m, height=h, color=SLATE_LIGHT, edgecolor=MUTED,
        linewidth=0.7, label="Before Dedup (occurrences, M)", zorder=3,
    )
    ax2.barh(
        y - h / 2, after_m, height=h, color=LINK, edgecolor=LINK,
        linewidth=0.7, label="Unique After Dedup (stored, M)", zorder=3,
    )

    for yi, bef, aft, pct in zip(y, before_m, after_m, m2_drop):
        ax2.text(
            bef + 0.07, yi + h / 2, f"{bef:.2f}M",
            va="center", ha="left", fontsize=7.9, color=INK, fontweight="medium",
        )
        ax2.text(
            aft + 0.07, yi - h / 2, f"{aft:.2f}M",
            va="center", ha="left", fontsize=7.9, color=LINK, fontweight="semibold",
        )
        ax2.text(
            4.46, yi, pct,
            va="center", ha="center", fontsize=7.9, color=LINK, fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.26,rounding_size=0.18",
                      facecolor=LINK_SOFT, edgecolor=LINK, linewidth=0.65),
        )

    ax2.set_yticks(y)
    ax2.set_yticklabels(["2409", "2501", "2503", "2504–05", "2506–07"],
                        fontsize=8.5, fontweight="medium", color=MUTED)
    ax2.set_xlim(0, 4.95)
    ax2.set_ylim(-0.75, 4.65)
    ax2.set_xticks([0, 1, 2, 3, 4])
    ax2.set_xlabel("Document Elements (Millions)", fontsize=8.7, labelpad=7, color=MUTED)
    ax2.set_title(
        "B. Milestone 2: Cross-Version Element Deduplication",
        loc="left", fontsize=10.5, fontweight="bold", color=INK, pad=20,
    )
    ax2.text(
        0.0, 1.032,
        "25,000-paper final corpus · total 16.22M to 11.10M elements (-31.6%)",
        transform=ax2.transAxes, fontsize=7.9, color=SOFT, ha="left", va="bottom",
    )
    ax2.legend(
        loc="upper center", bbox_to_anchor=(0.48, -0.185), ncol=2,
        frameon=False, fontsize=8.0, handlelength=1.2, columnspacing=1.1,
    )

    out_path = OUT_DIR / "m1_m2_stats.png"
    fig.savefig(out_path, dpi=300, facecolor=PAPER)
    plt.close(fig)
    print(f"Saved {out_path}")


def build_m3_feature_ablation():
    # --- Panel A Data: Single-Feature ROC-AUC on 1.56M train pairs ---
    # Exact verified numbers from report_brief.md Section 4.5 (1 - AUC for inverted rank/gap)
    feat_rows = [
        ("Gap to Best Candidate (inv.)", 1.0 - 0.0011, "group"),
        ("Within-Entry Title Rank (inv.)", 1.0 - 0.0022, "group"),
        ("Title Word Jaccard", 0.9964, "title"),
        ("Ref-in-Entry Containment", 0.9962, "title"),
        ("Char 3–5-gram TF-IDF Cosine", 0.9957, "title"),
        ("Within-Entry Score Z-Score", 0.9956, "group"),
        ("Title Partial Fuzzy Ratio", 0.9956, "title"),
        ("Mutual-Best Bidirectional", 0.9947, "group"),
        ("Author Surname Jaccard", 0.9862, "author"),
        ("First-Author Surname Match", 0.9753, "author"),
        ("Within-One-Year Tolerance", 0.7950, "year"),
        ("Exact arXiv ID Match", 0.6084, "id"),
    ]
    group_colors = {
        "group": ACCENT,
        "title": LINK,
        "author": MUTED,
        "year": SLATE_MED,
        "id": INK,
    }

    # --- Panel B Data: 5-Fold Group CV MRR@5 under 100% ID-Masking (n = 34,334 queries) ---
    # Exact verified numbers from report_brief.md Section 4.5
    abl_rows = [
        ("LambdaRank + ID-Mask Aug", 0.999100, "best"),
        ("LogReg + ID-Mask Aug", 0.998662, "aug"),
        ("LambdaRank (No Aug)", 0.997054, "base"),
        ("Selected LogReg (C = 0.1)", 0.997018, "selected"),
        ("---DIVIDER---", None, None),
        ("Drop Group Competition", 0.9988, "abl_up"),
        ("Drop Identifier Group", 0.9987, "abl_up"),
        ("Drop Title Lexical*", 0.9972, "abl_mid"),
        ("Drop Year Group", 0.9963, "abl_drop"),
        ("Drop Author Group", 0.9960, "abl_worst"),
    ]

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(12.2, 5.35), dpi=300,
        gridspec_kw={"width_ratios": [1.0, 1.04], "wspace": 0.52},
    )
    fig.subplots_adjust(left=0.195, right=0.975, top=0.84, bottom=0.205)

    # --- Panel A: Single-Feature ROC-AUC ---
    style_axes(ax1)
    ax1.xaxis.grid(True, linestyle="-", alpha=0.9)

    f_labels = [r[0] for r in feat_rows]
    f_vals = [r[1] for r in feat_rows]
    f_cols = [group_colors[r[2]] for r in feat_rows]
    y1 = np.arange(len(feat_rows))[::-1]

    ax1.barh(y1, f_vals, height=0.64, color=f_cols, edgecolor="none", alpha=0.92, zorder=3)
    for yi, val, col in zip(y1, f_vals, f_cols):
        ax1.text(
            val + 0.009, yi, f"{val:.4f}",
            va="center", ha="left", fontsize=7.9, color=col if col != SLATE_MED else MUTED,
            fontweight="semibold" if col in (ACCENT, LINK) else "medium",
            zorder=4,
        )

    ax1.set_yticks(y1)
    ax1.set_yticklabels(f_labels, fontsize=8.2, color=INK)
    ax1.set_xlim(0.50, 1.10)
    ax1.set_xticks([0.50, 0.60, 0.70, 0.80, 0.90, 1.00])
    ax1.set_xticklabels(["0.50", "0.60", "0.70", "0.80", "0.90", "1.00"], fontsize=8.0)
    ax1.set_ylim(-0.65, len(feat_rows) - 0.35)
    ax1.set_xlabel("Single-Feature ROC-AUC (0.50 = random; inv. = 1 − AUC)",
                   fontsize=8.3, labelpad=7, color=MUTED)
    ax1.set_title(
        "A. Single-Feature Discrimination Power",
        loc="left", fontsize=10.4, fontweight="bold", color=INK, pad=20,
    )
    ax1.text(
        0.0, 1.030,
        "1,561,952 training pairs · top 12 signals across all 5 groups",
        transform=ax1.transAxes, fontsize=7.8, color=SOFT, ha="left", va="bottom",
    )

    legend_handles_a = [
        Patch(facecolor=ACCENT, label="Group Competition"),
        Patch(facecolor=LINK, label="Title Lexical"),
        Patch(facecolor=MUTED, label="Author"),
        Patch(facecolor=SLATE_MED, label="Year"),
        Patch(facecolor=INK, label="Identifier"),
    ]
    ax1.legend(
        handles=legend_handles_a, loc="upper center", bbox_to_anchor=(0.46, -0.17),
        ncol=3, frameon=False, fontsize=7.7, handlelength=1.0, columnspacing=0.95,
        handletextpad=0.45,
    )

    # --- Panel B: 100% ID-Masked Robustness (Model & Group Ablation) ---
    style_axes(ax2)
    ax2.xaxis.grid(True, linestyle="-", alpha=0.9)

    b_y = []
    b_labels = []
    b_vals = []
    b_kinds = []
    divider_y = None
    cur_y = 9.5
    for label, val, kind in abl_rows:
        if label == "---DIVIDER---":
            divider_y = cur_y + 0.08
            cur_y -= 1.05
            continue
        b_y.append(cur_y)
        b_labels.append(label)
        b_vals.append(val)
        b_kinds.append(kind)
        cur_y -= 1.0

    kind_color = {
        "best": ACCENT,
        "aug": LINK,
        "base": SLATE_MED,
        "selected": INK,
        "abl_up": LINK,
        "abl_mid": SLATE_MED,
        "abl_drop": "#c66c4c",
        "abl_worst": ACCENT,
    }
    base_val = 0.997018

    # Vertical reference line at Selected Full LogReg (0.997018)
    ax2.axvline(base_val, color=INK, linestyle="--", linewidth=0.9, alpha=0.55, zorder=2)

    for yi, val, kind in zip(b_y, b_vals, b_kinds):
        col = kind_color[kind]
        left_x = 0.9950
        width = val - left_x
        alpha = 0.95 if kind in ("best", "selected", "abl_worst") else 0.80
        ax2.barh(yi, width, left=left_x, height=0.60, color=col, alpha=alpha, zorder=3)
        weight = "bold" if kind in ("best", "selected", "abl_worst") else "medium"
        ax2.text(
            val + 0.00009, yi, f"{val:.4f}",
            va="center", ha="left", fontsize=8.0, color=col if col != SLATE_MED else MUTED,
            fontweight=weight, zorder=4,
        )

    # Clean subgroup separator + white-masked subgroup headers
    ax2.axhline(divider_y, color=RULE, linestyle="-", linewidth=0.9, zorder=1)
    ax2.text(
        0.99512, 10.12, "MODEL CONFIGURATIONS", fontsize=7.0, color=SOFT,
        fontweight="semibold", ha="left", va="bottom",
        bbox=dict(facecolor=CANVAS, edgecolor="none", pad=1.2), zorder=5,
    )
    ax2.text(
        0.99512, divider_y - 0.16, "GROUP ABLATION (VS SELECTED LOGREG)",
        fontsize=7.0, color=SOFT, fontweight="semibold", ha="left", va="top",
        bbox=dict(facecolor=CANVAS, edgecolor="none", pad=1.2), zorder=5,
    )

    ax2.text(
        0.99512, b_y[-1] - 0.62,
        "*Group-rank features retain title ordering in Drop Title.",
        fontsize=7.2, color=SOFT, style="italic", ha="left", va="top",
        bbox=dict(facecolor=CANVAS, edgecolor="none", pad=1.2), zorder=5,
    )

    ax2.set_yticks(b_y)
    ax2.set_yticklabels(b_labels, fontsize=8.2, color=INK)
    ax2.set_xlim(0.9950, 1.00025)
    ax2.set_xticks([0.995, 0.996, 0.997, 0.998, 0.999, 1.000])
    ax2.set_xticklabels(["0.995", "0.996", "0.997", "0.998", "0.999", "1.000"], fontsize=8.0)
    ax2.set_ylim(b_y[-1] - 1.05, 10.65)
    ax2.set_xlabel("5-Fold Group CV MRR@5 under 100% ID-Masking",
                   fontsize=8.3, labelpad=7, color=MUTED)
    ax2.set_title(
        "B. Model & Ablation Robustness (100% ID-Masked)",
        loc="left", fontsize=10.4, fontweight="bold", color=INK, pad=20,
    )
    ax2.text(
        0.0, 1.030,
        "34,334 matched CV queries · dashed line = Selected LogReg (0.9970)",
        transform=ax2.transAxes, fontsize=7.8, color=SOFT, ha="left", va="bottom",
    )

    legend_handles_b = [
        Patch(facecolor=ACCENT, label="Peak gain / largest drop"),
        Patch(facecolor=LINK, label="Improved masked"),
        Patch(facecolor=INK, label="Selected LogReg"),
    ]
    ax2.legend(
        handles=legend_handles_b, loc="upper center", bbox_to_anchor=(0.48, -0.17),
        ncol=3, frameon=False, fontsize=7.7, handlelength=1.0, columnspacing=0.95,
        handletextpad=0.45,
    )

    out_path = OUT_DIR / "m3_feature_ablation.png"
    fig.savefig(out_path, dpi=300, facecolor=PAPER)
    plt.close(fig)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    build_m1_m2_stats()
    build_m3_feature_ablation()
