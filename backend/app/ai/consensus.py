"""
DOCINT — Multi-pipeline field consensus engine

This is the backend core of the Orchestration page (as distinct from the
OCR page, which runs exactly one chosen pipeline): several pipelines run on
the same document, and this module reconciles their per-field results into
one consensus value, field by field — never a whole-document vote.

Resolution policy (mirrors the product's own "2-of-3 Model Quorum, fallback:
Dynamic Highest Conf" framing):
  - every pipeline that reported a field agrees              -> "unanimous"
  - a true majority agrees (e.g. 2-of-3)                      -> "majority"
  - no majority (e.g. all three disagree)                     -> "highest_confidence",
    and the field is flagged needs_review=True — this is the product's
    "Flagged Discrepancies / Verification Needed" case.
  - only one pipeline reported the field at all                -> "single_pipeline"

Pure functions only: no DB, no pipeline execution, no GPU/LLM calls. Takes
already-computed per-pipeline field values and produces the comparison —
see app/services/orchestrator.py for where real PipelineResult objects get
turned into the input shape this module expects.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field as dc_field


def _normalize(value) -> str:
    """Loose equality for voting: case/whitespace-insensitive string compare.
    The exact value from each pipeline is still shown in the comparison —
    only the vote itself is normalized, so "INV-001" and "inv-001 " agree."""
    if value is None:
        return ""
    return " ".join(str(value).split()).strip().lower()


@dataclass
class PipelineFieldValue:
    pipeline_name: str
    value: str | None
    confidence: float
    bbox_json: dict | None = None
    page_id: str | None = None


@dataclass
class FieldComparison:
    field_name: str
    values: list[PipelineFieldValue] = dc_field(default_factory=list)
    consensus_value: str | None = None
    consensus_confidence: float = 0.0
    agreement: bool = False       # True iff every pipeline that reported this field agreed
    resolution_rule: str = "no_data"  # "unanimous" | "majority" | "highest_confidence" | "single_pipeline" | "no_data"
    needs_review: bool = True


def compare_field_across_pipelines(
    field_name: str,
    values: list[PipelineFieldValue],
) -> FieldComparison:
    """One field's cross-pipeline comparison + consensus resolution."""
    if not values:
        return FieldComparison(field_name=field_name, values=[], needs_review=True, resolution_rule="no_data")

    if len(values) == 1:
        v = values[0]
        return FieldComparison(
            field_name=field_name,
            values=values,
            consensus_value=v.value,
            consensus_confidence=v.confidence,
            agreement=True,
            resolution_rule="single_pipeline",
            needs_review=False,
        )

    normalized = [_normalize(v.value) for v in values]
    counts = Counter(normalized)
    top_value, top_count = counts.most_common(1)[0]
    matching = [v for v in values if _normalize(v.value) == top_value]

    if top_count == len(values):
        consensus_confidence = sum(v.confidence for v in matching) / len(matching)
        return FieldComparison(
            field_name=field_name, values=values,
            consensus_value=matching[0].value, consensus_confidence=consensus_confidence,
            agreement=True, resolution_rule="unanimous", needs_review=False,
        )

    if top_count > len(values) / 2:
        consensus_confidence = sum(v.confidence for v in matching) / len(matching)
        return FieldComparison(
            field_name=field_name, values=values,
            consensus_value=matching[0].value, consensus_confidence=consensus_confidence,
            agreement=False, resolution_rule="majority", needs_review=False,
        )

    # No majority — fall back to the single highest-confidence value, but
    # flag it for human review (the product's "Flagged Discrepancies" case).
    best = max(values, key=lambda v: v.confidence)
    return FieldComparison(
        field_name=field_name, values=values,
        consensus_value=best.value, consensus_confidence=best.confidence,
        agreement=False, resolution_rule="highest_confidence", needs_review=True,
    )


def compare_pipeline_results(
    results_by_pipeline: dict[str, dict[str, tuple]],
) -> list[FieldComparison]:
    """
    results_by_pipeline: {"surya": {"invoice_number": ("INV-1", 0.95), ...}, "qwen": {...}}

    Returns one FieldComparison per field name reported by ANY pipeline, in
    first-seen order. A pipeline that didn't report a field is simply absent
    from that field's `values` — never treated as "this pipeline said empty
    string", which would wrongly count as a disagreeing vote.
    """
    all_field_names: list[str] = []
    seen: set[str] = set()
    for pipeline_fields in results_by_pipeline.values():
        for name in pipeline_fields:
            if name not in seen:
                seen.add(name)
                all_field_names.append(name)

    comparisons = []
    for field_name in all_field_names:
        values = [
            PipelineFieldValue(
                pipeline_name=pname,
                value=pfields[field_name][0],
                confidence=pfields[field_name][1],
                bbox_json=pfields[field_name][2],
                page_id=pfields[field_name][3],
            )
            for pname, pfields in results_by_pipeline.items()
            if field_name in pfields
        ]
        comparisons.append(compare_field_across_pipelines(field_name, values))
    return comparisons


def overall_consensus_rate(comparisons: list[FieldComparison]) -> float:
    """Fraction of fields that resolved without needing review — the "38/42
    full consensus" style summary stat the Orchestration page's header shows."""
    if not comparisons:
        return 0.0
    resolved = sum(1 for c in comparisons if not c.needs_review)
    return resolved / len(comparisons)
