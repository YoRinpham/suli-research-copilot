"""Synthetic M4 review records. Expected labels are test targets, never automatic judgments."""

import json
from pathlib import Path

from app.prompts.m4 import GAP_PROMPT, HYPOTHESIS_PROMPT, PLAN_PROMPT


CASE_FILE = Path(__file__).with_name("m4_cases.json")
PROMPTS = {"gap": GAP_PROMPT, "hypothesis": HYPOTHESIS_PROMPT, "experiment_plan": PLAN_PROMPT}


def load_cases() -> list[dict[str, str]]:
    return json.loads(CASE_FILE.read_text(encoding="utf-8"))


def evaluation_record(case: dict[str, str], model: str) -> dict[str, object]:
    prompt = PROMPTS[case["stage"]]
    return {"scenario_id": case["id"], "stage": case["stage"], "model": model,
            "prompt_version": prompt.version, "prompt_bundle_hash": prompt.digest,
            "generation_run_id": None, "provider_attempt_count": 0, "generation_latency_ms": None,
            "generator_proposal": None, "source_validity": None, "ai_assessment": None,
            "human_decision": None, "final_downstream_eligibility": None,
            "human_evaluation": {"label": None, "label_source": "HUMAN evaluation",
                                 "evaluation_dimensions": None},
            "expected_probe_label": case["expected_human_label"]}
