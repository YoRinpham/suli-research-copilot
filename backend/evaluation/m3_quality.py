"""Controlled synthetic M3 quality probe. Run only with configured Qwen credentials."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from app.config.settings import get_settings
from app.prompts.scientific import CLAIM_PROMPT, LANDSCAPE_PROMPT
from app.providers.llm import DashScopeLLMProvider
from app.services.scientific import ScientificService
from app.services.scientific_schemas import ClaimBatch, LandscapeOutput


def human_evaluation_template() -> dict[str, object]:
    """Blank fields are human judgments, never automated scientific truth labels."""
    return {"label_source": "HUMAN evaluation", "manually_selected_count": None,
            "manually_rejected_count": None,
            "claim_counts": {name: None for name in ("GROUNDED", "GROUNDED_WITH_CAVEAT", "OVERCLAIM", "UNSUPPORTED")},
            "conflict_counts": {name: None for name in ("TRUE_CONFLICT", "CONDITIONAL_DIFFERENCE",
                                                         "FALSE_CONFLICT", "UNCLEAR")}}


def evaluation_record(case: dict[str, object], stage: str, model: str) -> dict[str, object]:
    return {"scenario_id": case["id"], "stage": stage, "input_evidence_count": len(case["evidence"]),
            "model": model, "prompt_versions": {"claim": CLAIM_PROMPT.version, "landscape": LANDSCAPE_PROMPT.version},
            "prompt_bundle_hashes": {"claim": CLAIM_PROMPT.digest, "landscape": LANDSCAPE_PROMPT.digest},
            "generation_run_id": None, "provider_attempt_count": 0,
            "expected": case["expected"], "claim_schema_valid": False, "claim_source_ids_valid": False,
            "landscape_schema_valid": None, "landscape_source_ids_valid": None,
            "generated_claim_count": 0,
            "generator_proposal": {"claims": None, "landscape": None},
            "ai_verifier_assessment": {"claim_grounding": None, "landscape_conflicts": None,
                                       "landscape_summary": None, "risk_flags": None},
            "human_scientific_judgment": {"claim_grounding": None, "claim_selection": None,
                                          "landscape_review": None, "label_source": "HUMAN evaluation"},
            "final_downstream_eligibility": {"claims": None, "landscape": None},
            "human_evaluation": human_evaluation_template()}


def evaluate(output_path: Path, case_ids: set[str] | None = None, timeout_seconds: int | None = None,
             retry_count: int | None = None, stage: str = "both") -> None:
    settings = get_settings()
    if settings.qwen_api_key is None:
        raise SystemExit("Production Qwen smoke test: NOT VERIFIED — CREDENTIALS UNAVAILABLE")
    provider = DashScopeLLMProvider(settings.qwen_api_key.get_secret_value(), "qwen3.8-max",
                                    timeout_seconds or settings.provider_timeout_seconds,
                                    settings.retry_count if retry_count is None else retry_count,
                                    base_url=settings.qwen_base_url)
    cases = json.loads((Path(__file__).parent / "m3_cases.json").read_text(encoding="utf-8"))
    if case_ids:
        cases = [case for case in cases if case["id"] in case_ids]
    results = []
    for case in cases:
        started = monotonic()
        allowed_evidence = {item["id"] for item in case["evidence"]}
        record = evaluation_record(case, stage, provider.model_name)
        try:
            if stage in ("both", "claim"):
                result = provider.generate_structured(CLAIM_PROMPT.text, ClaimBatch,
                                                       {"research_question": case["question"], "evidence": case["evidence"]})
                record["provider_attempt_count"] += result.attempt_count
                claims = result.output
                record["claim_schema_valid"] = True
                ScientificService._validate_claim_output(claims, allowed_evidence)
                record.update({"claim_source_ids_valid": True,
                               "generated_claim_count": len(claims.claims), "claims": claims.model_dump()})
                record["generator_proposal"]["claims"] = claims.model_dump()
            if stage in ("both", "landscape") and case.get("claims"):
                record["input_review_provenance"] = "Synthetic case Claims supplied as test context; no researcher reviewed them"
                context_claims = [{"id": item["id"], "text": item["text"], "claim_type": "finding",
                                   "human_grounding_status": "synthetic_fixture_only", "uncertainty": "none_identified",
                                   "evidence_for_ids": item["evidence_ids"], "evidence_against_ids": []}
                                  for item in case["claims"]]
                result = provider.generate_structured(LANDSCAPE_PROMPT.text, LandscapeOutput,
                                                      {"research_question": case["question"], "claims": context_claims,
                                                       "evidence": case["evidence"]})
                record["provider_attempt_count"] += result.attempt_count
                landscape = result.output
                provenance = {item["id"]: set(item["evidence_ids"]) for item in case["claims"]}
                record["landscape_schema_valid"] = True
                record["landscape_source_ids_valid"] = False
                ScientificService._validate_landscape_output(landscape, set(provenance), allowed_evidence, provenance)
                record.update({"landscape_source_ids_valid": True,
                               "landscape": landscape.model_dump()})
                record["generator_proposal"]["landscape"] = landscape.model_dump()
        except Exception as exc:
            record["provider_attempt_count"] += getattr(exc, "attempt_count", 0)
            record["error_type"] = type(exc).__name__
            record["provider_cause_type"] = type(exc.__cause__).__name__ if exc.__cause__ else None
            response = getattr(exc.__cause__, "response", None)
            if response is not None:
                record["provider_http_status"] = response.status_code
                try:
                    record["provider_error_code"] = str(response.json().get("code", "unknown"))[:80]
                except (ValueError, AttributeError):
                    record["provider_error_code"] = "non_json"
        record["latency_seconds"] = round(monotonic() - started, 3)
        results.append(record)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(), "cases": results},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(results)} synthetic evaluation records to {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--retry-count", type=int)
    parser.add_argument("--stage", choices=("both", "claim", "landscape"), default="both")
    args = parser.parse_args()
    evaluate(args.output, set(args.case_id), args.timeout_seconds, args.retry_count, args.stage)
