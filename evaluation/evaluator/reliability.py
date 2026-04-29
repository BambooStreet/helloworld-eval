from collections.abc import Iterable
from typing import Any

import krippendorff
import numpy as np

from .judges import DIMENSIONS, ConversationEvaluation


def krippendorff_alpha_per_dimension(
    evaluations: Iterable[ConversationEvaluation],
) -> dict[str, float]:
    """Compute Krippendorff's α (ordinal) for each dimension, using the N=k
    repetitions per conversation as raters and conversations as items.

    Returns a dict {dimension: alpha}. NaN means α was undefined (e.g., all
    raters agree perfectly across all items so disagreement is 0)."""
    evals = list(evaluations)
    out: dict[str, float] = {}
    if not evals:
        return {dim: float("nan") for dim in DIMENSIONS}

    n_raters = evals[0].n_repetitions
    if n_raters < 2:
        return {dim: float("nan") for dim in DIMENSIONS}

    for dim in DIMENSIONS:
        ratings = np.zeros((n_raters, len(evals)), dtype=float)
        for i, ev in enumerate(evals):
            scores = [s.score for s in ev.dimensions[dim].scores]
            ratings[:, i] = scores
        try:
            alpha = float(
                krippendorff.alpha(
                    reliability_data=ratings,
                    level_of_measurement="ordinal",
                )
            )
        except (ValueError, ZeroDivisionError):
            alpha = float("nan")
        out[dim] = alpha
    return out


def adversarial_score_diff(
    good_evaluations: Iterable[ConversationEvaluation],
    bad_evaluations: Iterable[ConversationEvaluation],
) -> dict[str, dict[str, float]]:
    """For each dimension, compute mean(good) - mean(bad). Acceptance threshold ≥ 2.0."""
    good = list(good_evaluations)
    bad = list(bad_evaluations)
    out: dict[str, dict[str, float]] = {}
    for dim in DIMENSIONS:
        good_means = [ev.dimensions[dim].mean for ev in good] if good else []
        bad_means = [ev.dimensions[dim].mean for ev in bad] if bad else []
        good_mean = float(np.mean(good_means)) if good_means else float("nan")
        bad_mean = float(np.mean(bad_means)) if bad_means else float("nan")
        diff = (
            good_mean - bad_mean
            if not (np.isnan(good_mean) or np.isnan(bad_mean))
            else float("nan")
        )
        out[dim] = {
            "good_mean": good_mean,
            "bad_mean": bad_mean,
            "diff": diff,
        }
    return out


def aggregate_summary(
    evaluations: Iterable[ConversationEvaluation],
) -> dict[str, dict[str, float]]:
    """Per-dimension aggregate statistics across all evaluated conversations.
    Each conversation contributes its dimension median to the population."""
    evals = list(evaluations)
    out: dict[str, dict[str, float]] = {}
    for dim in DIMENSIONS:
        medians = [ev.dimensions[dim].median for ev in evals]
        means = [ev.dimensions[dim].mean for ev in evals]
        if not medians:
            out[dim] = {"mean": float("nan"), "median": float("nan"), "stddev": float("nan")}
            continue
        out[dim] = {
            "mean": float(np.mean(means)),
            "median": float(np.median(medians)),
            "stddev": float(np.std(medians)) if len(medians) > 1 else 0.0,
        }
    return out


def per_persona_summary(
    evaluations: Iterable[ConversationEvaluation],
) -> dict[str, dict[str, Any]]:
    """Per-persona average score across all dimensions. Returns
    {seed_id: {n_conversations, overall_mean, dim_means}}."""
    grouped: dict[str, list[ConversationEvaluation]] = {}
    for ev in evaluations:
        grouped.setdefault(ev.seed_id, []).append(ev)

    out: dict[str, dict[str, Any]] = {}
    for seed_id, group in grouped.items():
        dim_means: dict[str, float] = {}
        all_dim_values: list[float] = []
        for dim in DIMENSIONS:
            vals = [ev.dimensions[dim].mean for ev in group]
            mean_val = float(np.mean(vals)) if vals else float("nan")
            dim_means[dim] = mean_val
            all_dim_values.extend(vals)
        out[seed_id] = {
            "n_conversations": len(group),
            "overall_mean": float(np.mean(all_dim_values)) if all_dim_values else float("nan"),
            "dim_means": dim_means,
        }
    return out
