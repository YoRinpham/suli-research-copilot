"""Unfilled M5 evaluation record shape; no fabricated benchmark measurements."""

from typing import Any


def evaluation_record(case_id: str, area: str, *, proposal: dict[str, Any] | None = None,
                      server_result: dict[str, Any] | None = None,
                      generation_run_id: str | None = None,
                      deterministic_run_id: str | None = None) -> dict[str, Any]:
    return {
        "case_id": case_id, "area": area, "proposal": proposal,
        "server_result": server_result, "generation_run_id": generation_run_id,
        "deterministic_run_id": deterministic_run_id,
        "source_valid": None, "numeric_correct": None,
        "ai_assessment": None, "human_review": None,
        "scientific_quality_label": None,
        "started_at_utc": None, "completed_at_utc": None,
    }
