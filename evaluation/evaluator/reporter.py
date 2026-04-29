import math
from datetime import UTC, datetime
from typing import Any

from .judges import DIMENSIONS, ConversationEvaluation
from .reliability import (
    adversarial_score_diff,
    aggregate_summary,
    krippendorff_alpha_per_dimension,
    per_persona_summary,
)


def _fmt(value: float, digits: int = 2) -> str:
    if math.isnan(value):
        return "—"
    return f"{value:.{digits}f}"


def render_report(
    evaluations: list[ConversationEvaluation],
    *,
    run_id: str,
    total_cost_usd: float,
    total_tokens: int,
    duration_seconds: float,
    n_repetitions: int,
    good_evaluations: list[ConversationEvaluation] | None = None,
    bad_evaluations: list[ConversationEvaluation] | None = None,
    alpha_threshold: float = 0.67,
) -> str:
    lines: list[str] = []
    now = datetime.now(UTC).isoformat(timespec="seconds")
    lines.append("# Evaluation Report")
    lines.append("")
    lines.append(f"- Run: `{run_id}`")
    lines.append(f"- Generated: {now}")
    lines.append(f"- Conversations evaluated: **{len(evaluations)}**")
    lines.append(f"- Personas: {len({ev.seed_id for ev in evaluations})}")
    lines.append(f"- Dimensions: {len(DIMENSIONS)}")
    lines.append(f"- N repetitions per dimension: {n_repetitions}")
    total_calls = len(evaluations) * len(DIMENSIONS) * n_repetitions
    if good_evaluations or bad_evaluations:
        adv_count = len(good_evaluations or []) + len(bad_evaluations or [])
        total_calls += adv_count * len(DIMENSIONS) * n_repetitions
    lines.append(f"- Total judge LLM calls: {total_calls}")
    lines.append(f"- Total tokens: {total_tokens}")
    lines.append(f"- Total cost: **${total_cost_usd:.6f}**")
    lines.append(f"- Total duration: {duration_seconds:.1f}s")
    lines.append("")

    # Per-dimension aggregates
    lines.append("## Per-dimension aggregates (across all conversations)")
    lines.append("")
    lines.append("| Dimension | Mean | Median | Stddev | Krippendorff α | α ≥ 0.67? |")
    lines.append("|---|---|---|---|---|---|")
    agg = aggregate_summary(evaluations)
    alphas = krippendorff_alpha_per_dimension(evaluations)
    for dim in DIMENSIONS:
        a = agg[dim]
        alpha = alphas.get(dim, float("nan"))
        passes = (
            "✅"
            if (not math.isnan(alpha)) and alpha >= alpha_threshold
            else ("⚠️" if not math.isnan(alpha) else "—")
        )
        lines.append(
            f"| {dim} | {_fmt(a['mean'])} | {_fmt(a['median'])} | "
            f"{_fmt(a['stddev'])} | {_fmt(alpha)} | {passes} |"
        )
    lines.append("")
    weak = [dim for dim, v in alphas.items() if not math.isnan(v) and v < alpha_threshold]
    if weak:
        lines.append(
            f"> ⚠️ Krippendorff α below {alpha_threshold} in: **{', '.join(weak)}**. "
            f"Consider sharpening the rubric for these dimensions."
        )
        lines.append("")

    # Per-persona aggregates
    lines.append("## Per-persona aggregates")
    lines.append("")
    persona_agg = per_persona_summary(evaluations)
    if persona_agg:
        lines.append("| Persona (seed_id) | N convs | Overall mean | Strongest dim | Weakest dim |")
        lines.append("|---|---|---|---|---|")
        for seed_id, data in sorted(persona_agg.items()):
            dim_means: dict[str, float] = data["dim_means"]
            valid = {k: v for k, v in dim_means.items() if not math.isnan(v)}
            if valid:
                strongest = max(valid.items(), key=lambda kv: kv[1])
                weakest = min(valid.items(), key=lambda kv: kv[1])
                strongest_s = f"{strongest[0]} ({_fmt(strongest[1])})"
                weakest_s = f"{weakest[0]} ({_fmt(weakest[1])})"
            else:
                strongest_s = weakest_s = "—"
            lines.append(
                f"| {seed_id} | {data['n_conversations']} | "
                f"{_fmt(data['overall_mean'])} | {strongest_s} | {weakest_s} |"
            )
        lines.append("")

    # Adversarial sanity
    if good_evaluations or bad_evaluations:
        lines.append("## Adversarial sanity check")
        lines.append("")
        diffs = adversarial_score_diff(good_evaluations or [], bad_evaluations or [])
        lines.append("| Dimension | Good mean | Bad mean | Diff | Pass (≥ 2.0)? |")
        lines.append("|---|---|---|---|---|")
        for dim in DIMENSIONS:
            d = diffs[dim]
            passes = (
                "✅"
                if (not math.isnan(d["diff"])) and d["diff"] >= 2.0
                else ("⚠️" if not math.isnan(d["diff"]) else "—")
            )
            lines.append(
                f"| {dim} | {_fmt(d['good_mean'])} | {_fmt(d['bad_mean'])} | "
                f"{_fmt(d['diff'])} | {passes} |"
            )
        lines.append("")

    # Per-conversation detail
    lines.append("## Per-conversation detail")
    lines.append("")
    for ev in evaluations:
        lines.append(f"### `{ev.run_id}` — `{ev.seed_id}` ({ev.transcript_turn_count} turns)")
        lines.append("")
        lines.append("| Dimension | Median | Mean | Scores | Rationales (excerpts) |")
        lines.append("|---|---|---|---|---|")
        for dim in DIMENSIONS:
            dr = ev.dimensions[dim]
            scores_s = ", ".join(str(s.score) for s in dr.scores)
            rationales = " ⏐ ".join(
                _truncate(s.rationale, 80) for s in dr.scores
            )
            lines.append(
                f"| {dim} | {_fmt(dr.median, 1)} | {_fmt(dr.mean)} | {scores_s} | {rationales} |"
            )
        lines.append("")

    # Failure case excerpts (lowest-scoring conversation per dimension)
    lines.append("## Failure case excerpts")
    lines.append("")
    failures = _failure_excerpts(evaluations)
    if not failures:
        lines.append("- (no clear low-scoring case found)")
    else:
        for entry in failures:
            lines.append(
                f"- **{entry['dimension']}** lowest median **{_fmt(entry['median'], 1)}** in "
                f"`{entry['seed_id']}` (run `{entry['run_id']}`): "
                f"{_truncate(entry['rationale'], 200)}"
            )
    lines.append("")

    return "\n".join(lines)


def _truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def _failure_excerpts(
    evaluations: list[ConversationEvaluation],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for dim in DIMENSIONS:
        worst: ConversationEvaluation | None = None
        worst_median = math.inf
        for ev in evaluations:
            m = ev.dimensions[dim].median
            if m < worst_median:
                worst_median = m
                worst = ev
        if worst and worst_median < 4.0:
            scores = worst.dimensions[dim].scores
            rationale = scores[0].rationale if scores else ""
            out.append(
                {
                    "dimension": dim,
                    "median": worst_median,
                    "seed_id": worst.seed_id,
                    "run_id": worst.run_id,
                    "rationale": rationale,
                }
            )
    return out
