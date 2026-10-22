"""Human-operated M6 timer and factual benchmark report.

Run from backend: python evaluation/m6_benchmark.py record --scenario PATH --mode manual --output PATH
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.services.m6_benchmark import BenchmarkSession, derive_copilot_metrics, markdown_report, report_from_files


def record(scenario_path: Path, mode: str, output: Path, project_id: str | None = None) -> None:
    raw = scenario_path.read_bytes()
    scenario = json.loads(raw)
    stages = scenario["stages"]
    if not scenario.get("scenario_id") or not scenario.get("task") or not stages:
        raise ValueError("Scenario must identify ID, task and stages")
    if scenario.get("protocol_version") != "m6-v1":
        raise ValueError("Unsupported benchmark protocol")
    for asset in [*scenario["corpus_manifest"], scenario["dataset_manifest"]]:
        path = (scenario_path.parent / asset["path"]).resolve()
        if not path.is_relative_to(scenario_path.parent.resolve()) or sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            raise ValueError(f"Input manifest hash mismatch: {asset['path']}")
    print(f"Scenario: {scenario['scenario_id']} ({mode})")
    print("每个阶段按回车开始，完成后再按回车结束；请如实记录失败。")
    events = []
    for stage in stages:
        input(f"{stage}: 按回车开始")
        started = datetime.now(timezone.utc)
        input(f"{stage}: 按回车结束")
        completed = datetime.now(timezone.utc)
        failed = input("本阶段失败？输入 y 表示失败，否则回车：").strip().lower() == "y"
        events.append({"stage": stage, "started_at": started.isoformat(),
                       "completed_at": completed.isoformat(),
                       "status": "failed" if failed else "succeeded",
                       "notes": input("阶段备注（可空）：").strip(),
                       "human_decisions": int(input("本阶段人工科学选择/审核次数（非点击数）：").strip())})
    decisions = sum(event["human_decisions"] for event in events)
    metrics = None
    if mode == "research_copilot":
        if not project_id:
            raise ValueError("SULI mode requires --project-id for persisted-run counts")
        engine = create_engine(get_settings().database_url)
        try:
            with Session(engine) as database:
                metrics = derive_copilot_metrics(database, project_id,
                    datetime.fromisoformat(min(event["started_at"] for event in events)),
                    datetime.fromisoformat(max(event["completed_at"] for event in events)))
        finally:
            engine.dispose()
    session = BenchmarkSession.model_validate({"session_id": str(uuid4()),
        "scenario_id": scenario["scenario_id"], "task": scenario["task"],
        "input_hash": sha256(raw).hexdigest(), "mode": mode,
        "events": events, "human_decisions": decisions, "copilot_metrics": metrics,
        "notes": input("会话备注（可空）：").strip()})
    if output.exists():
        raise FileExistsError("Refusing to overwrite a recorded session")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(session.model_dump_json(indent=2), encoding="utf-8")
    print(f"Saved: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Versioned M6 measured workflow benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    record_command = commands.add_parser("record")
    record_command.add_argument("--scenario", type=Path, required=True)
    record_command.add_argument("--mode", choices=["manual", "research_copilot"], required=True)
    record_command.add_argument("--output", type=Path, required=True)
    record_command.add_argument("--project-id")
    summary = commands.add_parser("summarize")
    summary.add_argument("--manual", type=Path)
    summary.add_argument("--copilot", type=Path)
    summary.add_argument("--json", type=Path, required=True)
    summary.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "record":
        record(args.scenario, args.mode, args.output, args.project_id)
    else:
        result = report_from_files(args.manual, args.copilot)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        args.markdown.write_text(markdown_report(result), encoding="utf-8")
        print(markdown_report(result))


if __name__ == "__main__":
    main()
