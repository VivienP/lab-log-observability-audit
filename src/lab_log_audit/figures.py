"""Deterministic figure generation for the audit.

Matplotlib is imported lazily so that importing the analysis modules stays cheap
and dependency-light. PNG metadata is suppressed so a rerun against identical
inputs is byte-stable.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .background import NullSummary

FIGURE_DPI = 120
FIGURE_SIZE = (9.0, 5.6)

_OBSERVED_COLOUR = "#b2182b"
_NULL_COLOUR = "#4d4d4d"
_BAND_COLOUR = "#bdbdbd"


def _window_label(summary: NullSummary) -> str:
    if summary.pre_window_seconds == summary.post_window_seconds:
        return f"±{summary.pre_window_seconds} s"
    return f"[-{summary.pre_window_seconds} s, +{summary.post_window_seconds} s]"


def render_background_figure(path: Path, summaries: Sequence[NullSummary]) -> Path:
    """Plot observed coverage against the random-anchor null for each window.

    One column per temporal window: the grey band is the 5th-95th percentile of
    the null distribution, the bar inside it is the null median, and the red
    marker is the observed coverage on the same recovery set.
    """
    if not summaries:
        raise ValueError("at least one null summary is required")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(figsize=FIGURE_SIZE, dpi=FIGURE_DPI)
    positions = range(len(summaries))
    band = null_bar = observed_marker = None
    for position, summary in zip(positions, summaries, strict=True):
        low = summary.null_percentiles["p05"]
        high = summary.null_percentiles["p95"]
        band = axes.add_patch(
            plt.Rectangle(
                (position - 0.20, low),
                0.40,
                max(high - low, 1e-4),
                facecolor=_BAND_COLOUR,
                edgecolor=_NULL_COLOUR,
                linewidth=0.8,
                zorder=2,
            )
        )
        null_bar = axes.hlines(
            summary.null_percentiles["p50"],
            position - 0.20,
            position + 0.20,
            color=_NULL_COLOUR,
            linewidth=1.6,
            zorder=3,
        )
        axes.vlines(
            position,
            summary.null_min,
            summary.null_max,
            color=_NULL_COLOUR,
            linewidth=0.8,
            zorder=1,
        )
        (observed_marker,) = axes.plot(
            position,
            summary.observed_fraction,
            marker="D",
            markersize=8,
            linestyle="none",
            color=_OBSERVED_COLOUR,
            zorder=4,
        )
        ratio = summary.ratio_observed_over_expected
        ratio_text = "ratio n/a" if ratio is None else f"{ratio:.2f}×"
        axes.annotate(
            f"observed {summary.observed_matched}/{summary.analysed_recoveries}"
            f" = {summary.observed_fraction:.1%}\n"
            f"null mean {summary.expected_fraction:.1%}\n"
            f"observed/null {ratio_text}\n"
            f"empirical p = {summary.empirical_p_value:.4f}",
            (position, 0.985),
            xycoords=("data", "axes fraction"),
            ha="center",
            va="top",
            fontsize=8.5,
            color="#222222",
            linespacing=1.45,
        )

    reference = summaries[0]
    axes.set_xticks(list(positions))
    axes.set_xticklabels([_window_label(summary) for summary in summaries])
    axes.set_xlim(-0.55, len(summaries) - 0.45)
    axes.set_ylim(0.0, 1.06)
    axes.set_yticks([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7])
    axes.set_ylabel("Recoveries with ≥1 operation-log event in window")
    axes.set_xlabel("Temporal window around the recovery anchor")
    axes.set_title(
        "Recovery labels with nearby log activity, against a random-anchor background",
        fontsize=11,
        loc="left",
    )
    axes.yaxis.grid(True, linestyle=":", linewidth=0.6, color="#cccccc")
    axes.set_axisbelow(True)
    for spine in ("top", "right"):
        axes.spines[spine].set_visible(False)
    axes.legend(
        [observed_marker, null_bar, band],
        [
            "observed (labelled recovery anchors)",
            "null median (random anchors)",
            "null 5th-95th percentile; whisker = null range",
        ],
        loc="center left",
        fontsize=8,
        frameon=False,
        bbox_to_anchor=(0.0, 0.70),
    )
    figure.text(
        0.012,
        0.070,
        "Null: each anchor is redrawn uniformly inside its own experiment's observable "
        "operation-log interval, keeping that\n"
        "experiment's event structure and the window fixed "
        f"({reference.anchor_domain_mode} anchor domain, {reference.resampling} resampling,\n"
        f"{reference.iterations:,} iterations, seed {reference.seed}, "
        f"n = {reference.analysed_recoveries} of 79 recoveries; the other 5 are anchored outside "
        "their experiment's interval).\n"
        "Temporal association only. A ratio above one is not causal evidence and does not show "
        "that the labelled\nphysical or operator recovery was itself observed.",
        fontsize=7,
        color="#555555",
        linespacing=1.5,
    )
    figure.subplots_adjust(left=0.085, right=0.985, top=0.93, bottom=0.30)

    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, format="png", metadata={"Software": None})
    plt.close(figure)
    return path
