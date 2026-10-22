"""Versioned, factual M6 timing format and comparison arithmetic."""

from datetime import datetime
from pathlib import Path
from hashlib import sha256
import json
import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import AnalysisRun, CleaningRun, GenerationRun, VisualizationSpec


PROTOCOL_VERSION = "m6-v1"
SCENARIO_PATH = Path(__file__).resolve().parents[2] / "evaluation" / "m6_scenario.json"


def scenario_contract() -> tuple[dict, str]:
    raw = SCENARIO_PATH.read_bytes()
    scenario = json.loads(raw)
    stages = scenario.get("stages", [])
    if (scenario.get("protocol_version") != PROTOCOL_VERSION or not stages or
            len(stages) != len(set(stages)) or not all(isinstance(stage, str) and stage for stage in stages)):
        raise ValueError("Benchmark scenario stage contract is invalid")
    for asset in [*scenario["corpus_manifest"], scenario["dataset_manifest"]]:
        path = (SCENARIO_PATH.parent / asset["path"]).resolve()
        if (not path.is_relative_to(SCENARIO_PATH.parent.resolve()) or
                sha256(path.read_bytes()).hexdigest() != asset["sha256"]):
            raise ValueError("Benchmark input manifest hash is invalid")
    return scenario, sha256(raw).hexdigest()


class BenchmarkEvent(BaseModel):
    stage: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime
    status: Literal["succeeded", "failed"]
    notes: str = ""
    human_decisions: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def ordered(self):
        if self.started_at.tzinfo is None or self.completed_at.tzinfo is None:
            raise ValueError("Benchmark event timing requires timezone-aware timestamps")
        if self.completed_at < self.started_at:
            raise ValueError("Event completion precedes start")
        return self

    @property
    def duration_seconds(self) -> float:
        return (self.completed_at - self.started_at).total_seconds()


class BenchmarkSession(BaseModel):
    session_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    task: str = Field(min_length=1)
    input_hash: str = Field(min_length=64, max_length=64)
    protocol_version: Literal["m6-v1"] = PROTOCOL_VERSION
    mode: Literal["manual", "research_copilot"]
    events: list[BenchmarkEvent] = Field(min_length=1)
    human_decisions: int = Field(ge=0)
    notes: str = ""
    copilot_metrics: dict[str, int | float] | None = None

    @model_validator(mode="after")
    def decisions_match_events(self):
        if self.human_decisions != sum(event.human_decisions for event in self.events):
            raise ValueError("Human decision count must match recorded stage decisions")
        scenario, digest = scenario_contract()
        if (self.scenario_id != scenario["scenario_id"] or self.task != scenario["task"] or
                self.protocol_version != scenario["protocol_version"] or self.input_hash != digest):
            raise ValueError("Benchmark scenario, task, protocol or input hash is invalid")
        required = scenario["stages"]
        recorded = [event.stage for event in self.events]
        if len(recorded) != len(set(recorded)) or any(stage not in required for stage in recorded):
            raise ValueError("Benchmark contains duplicate or unknown stages")
        if recorded != [stage for stage in required if stage in recorded]:
            raise ValueError("Benchmark stages are out of protocol order")
        if any(current.started_at < previous.completed_at for previous, current in zip(self.events, self.events[1:])):
            raise ValueError("Benchmark stage timings overlap or are out of order")
        return self

    @property
    def missing_stages(self) -> list[str]:
        required, _ = scenario_contract()
        return [stage for stage in required["stages"] if stage not in {event.stage for event in self.events}]

    @property
    def invalid_stages(self) -> list[str]:
        return [event.stage for event in self.events if event.status != "succeeded" or event.duration_seconds <= 0]

    @property
    def complete(self) -> bool:
        return not self.missing_stages and not self.invalid_stages

    @property
    def status(self) -> str:
        return "completed" if self.complete else "incomplete"

    @property
    def duration_seconds(self) -> float:
        return (max(e.completed_at for e in self.events) - min(e.started_at for e in self.events)).total_seconds()

    def summary(self) -> dict:
        return {"session_id": self.session_id, "scenario_id": self.scenario_id,
                "input_hash": self.input_hash, "protocol_version": self.protocol_version,
                "mode": self.mode, "status": self.status, "duration_seconds": self.duration_seconds,
                "complete": self.complete, "missing_stages": self.missing_stages,
                "invalid_stages": self.invalid_stages,
                "human_decisions": self.human_decisions,
                "copilot_metrics": self.copilot_metrics,
                "measured_at": max(e.completed_at for e in self.events).isoformat(),
                "stages": [{"stage": e.stage, "duration_seconds": e.duration_seconds,
                            "status": e.status, "human_decisions": e.human_decisions} for e in self.events]}


def compare_sessions(manual: dict | None, copilot: dict | None) -> dict:
    not_measured = {"comparison": "NOT MEASURED", "time_saved_seconds": None,
                    "percentage_reduction": None}
    if manual is None or copilot is None:
        return not_measured
    scenario, digest = scenario_contract()
    required = scenario["stages"]
    def eligible(summary: dict, mode: str) -> bool:
        stages = summary.get("stages")
        if not isinstance(stages, list) or [row.get("stage") for row in stages] != required:
            return False
        if any(row.get("status") != "succeeded" or
               not isinstance(row.get("duration_seconds"), (int, float)) or
               not math.isfinite(row["duration_seconds"]) or row["duration_seconds"] <= 0
               for row in stages):
            return False
        duration = summary.get("duration_seconds")
        return (summary.get("mode") == mode and summary.get("scenario_id") == scenario["scenario_id"] and
                summary.get("protocol_version") == scenario["protocol_version"] and
                summary.get("input_hash") == digest and summary.get("complete") is True and
                summary.get("status") == "completed" and isinstance(duration, (int, float)) and
                math.isfinite(duration) and duration > 0 and
                duration >= sum(row["duration_seconds"] for row in stages) - 1e-6)
    if not eligible(manual, "manual") or not eligible(copilot, "research_copilot"):
        return not_measured
    if (manual.get("scenario_id"), manual.get("input_hash"), manual.get("protocol_version")) != (
            copilot.get("scenario_id"), copilot.get("input_hash"), copilot.get("protocol_version")):
        return not_measured
    baseline, assisted = manual.get("duration_seconds"), copilot.get("duration_seconds")
    if not isinstance(baseline, (int, float)) or baseline <= 0:
        raise ValueError("Manual baseline must be positive")
    if not isinstance(assisted, (int, float)) or assisted <= 0:
        raise ValueError("SULI timing is invalid")
    saved = baseline - assisted
    return {"comparison": "MEASURED", "time_saved_seconds": saved,
            "percentage_reduction": saved / baseline * 100}


def derive_copilot_metrics(session: Session, project_id: str, started_at: datetime,
                           completed_at: datetime) -> dict[str, int | float]:
    runs = list(session.scalars(select(GenerationRun).where(GenerationRun.project_id == project_id,
        GenerationRun.created_at >= started_at, GenerationRun.created_at <= completed_at)))
    cleanings = list(session.scalars(select(CleaningRun).where(CleaningRun.project_id == project_id,
        CleaningRun.started_at >= started_at, CleaningRun.started_at <= completed_at)))
    analyses = list(session.scalars(select(AnalysisRun).where(AnalysisRun.project_id == project_id,
        AnalysisRun.started_at >= started_at, AnalysisRun.started_at <= completed_at)))
    visualizations = list(session.scalars(select(VisualizationSpec).where(VisualizationSpec.project_id == project_id,
        VisualizationSpec.created_at >= started_at, VisualizationSpec.created_at <= completed_at)))
    deterministic = [*cleanings, *analyses, *visualizations]
    def elapsed(item) -> float:
        start = getattr(item, "started_at", None) or getattr(item, "created_at")
        end = item.completed_at
        return max(0.0, (end - start).total_seconds()) if end else 0.0
    return {"generation_runs": len(runs), "provider_attempts": sum(row.attempt_count for row in runs),
            "failed_provider_runs": sum(row.status == "failed" for row in runs),
            "cleaning_runs": len(cleanings), "analysis_runs": len(analyses),
            "deterministic_execution_seconds": sum(elapsed(row) for row in deterministic)}


def report_from_files(manual_path: Path | None, copilot_path: Path | None) -> dict:
    def load(path: Path | None) -> dict | None:
        return BenchmarkSession.model_validate_json(path.read_text(encoding="utf-8")).summary() if path else None
    manual, copilot = load(manual_path), load(copilot_path)
    return {"manual": manual, "research_copilot": copilot,
            **compare_sessions(manual, copilot)}


def markdown_report(report: dict) -> str:
    manual, copilot = report["manual"], report["research_copilot"]
    example = manual or copilot
    rows = ["# M6 Benchmark", "", f"Scenario: {example['scenario_id'] if example else 'NOT MEASURED'}",
            f"Protocol: {example['protocol_version'] if example else PROTOCOL_VERSION}", "",
            "| Mode | Complete | Missing stages | Invalid stages | Measured at | Elapsed (s) | Human decisions |",
            "| --- | --- | --- | --- | --- | ---: | ---: |"]
    for label, value in (("Manual", manual), ("溯理 SULI", copilot)):
        rows.append(f"| {label} | {value['complete']} | {', '.join(value['missing_stages']) or 'none'} | {', '.join(value['invalid_stages']) or 'none'} | {value['measured_at']} | {value['duration_seconds']} | {value['human_decisions']} |" if value else
                    f"| {label} | NOT MEASURED | — | — | NOT MEASURED | NOT MEASURED | NOT MEASURED |")
    rows.extend(["", f"Time saved (s): {report['time_saved_seconds'] if report['comparison'] == 'MEASURED' else 'NOT MEASURED'}",
                 f"Time reduction (%): {report['percentage_reduction'] if report['comparison'] == 'MEASURED' else 'NOT MEASURED'}", ""])
    return "\n".join(rows)
