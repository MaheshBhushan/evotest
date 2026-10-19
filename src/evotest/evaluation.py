"""Run fixed tasks through the existing loop and measure feedback variants."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from evotest.benchmark.runner import BenchmarkRunner
from evotest.config import HarnessConfig
from evotest.feedback.representation import attempt_feedback
from evotest.orchestrator import ClosedLoopOrchestrator


class EvaluationRunner:
    def __init__(self, config: HarnessConfig) -> None:
        self.config = config.resolved()

    def _within_project(self, value: str) -> Path:
        candidate = (self.config.project_root / value).resolve()
        if not candidate.is_relative_to(self.config.project_root):
            raise ValueError("Evaluation paths must stay within the configured project.")
        return candidate

    def evaluate(self, target: str, feedback_format: str = "compact") -> dict[str, Any]:
        if feedback_format not in {"compact", "raw"}:
            raise ValueError("Feedback representation must be compact or raw.")
        target_path = self._within_project(target.split("::", 1)[0])
        if self.config.max_iterations < 1:
            raise ValueError("Evaluation requires at least one iteration.")
        if self.config.generator_mode == "llm" and not self.config.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for LLM evaluations.")
        if not target_path.is_file():
            raise ValueError(f"Target file not found: {target}")
        started = perf_counter()
        artifacts = self.config.artifacts_dir / "evaluations" / uuid4().hex
        config = replace(
            self.config,
            artifacts_root=artifacts,
            database_path=artifacts / "state.db",
            test_output_dirname="generated_tests",
            feedback_format=feedback_format,
        )
        orchestrator = ClosedLoopOrchestrator(config)
        result = orchestrator.run_target(target)
        elapsed = perf_counter() - started
        attempts = [attempt_feedback(attempt, feedback_format) for attempt in result.attempts]
        previous_coverage = None
        for attempt in attempts:
            current = attempt["coverage_percent"]
            attempt["coverage_delta"] = round(current - previous_coverage, 2) if current is not None and previous_coverage is not None else None
            previous_coverage = current
        initial = attempts[0]["coverage_percent"] if attempts else None
        final = result.final_coverage if any(a["coverage_percent"] is not None for a in attempts) else None
        repair_stats = BenchmarkRunner._repair_stats(result.attempts)
        repair_attempts = sum(bucket["attempts"] for bucket in repair_stats.values())
        repair_successes = sum(bucket["successes"] for bucket in repair_stats.values())
        provider = orchestrator.generator.provider
        records = getattr(provider, "usage_records", None) if provider else []
        usage = None
        if records is not None and all(record is not None for record in records):
            usage = {key: sum(record[key] for record in records) for key in ("input_tokens", "output_tokens")}
            usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        report = {
            "target": result.target_id,
            "language": result.language,
            "feedback_format": feedback_format,
            "generator_mode": config.generator_mode,
            "model": config.model_name if config.generator_mode != "heuristic" else None,
            "providers_used": sorted({a.provider_name or "heuristic" for a in result.attempts}),
            "fallback_attempts": sum(bool(a.fallback_reason) for a in result.attempts),
            "success": result.final_success,
            "stability_passed": result.stability_passed,
            "initial_coverage": initial,
            "final_coverage": final,
            "coverage_delta": round(final - initial, 2) if final is not None and initial is not None else None,
            "repair_attempts": repair_attempts,
            "repair_successes": repair_successes,
            "repair_success_rate": repair_successes / repair_attempts if repair_attempts else None,
            "token_usage": usage,
            "latency_seconds": elapsed,
            "attempts": attempts,
            "report_path": str(artifacts / "evaluation.json"),
        }
        Path(report["report_path"]).write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    def compare(self, spec: str) -> dict[str, Any]:
        spec_path = self._within_project(spec)
        specs = BenchmarkRunner(self.config).load_specs(spec_path)
        if not 1 <= len(specs) <= 20:
            raise ValueError("A comparison spec must contain 1 to 20 fixed tasks.")
        # Validate every task before running either representation.
        for task in specs:
            self._within_project(str(task.project_root))
            target_path = (task.project_root / task.target_ref.split("::", 1)[0]).resolve()
            if not target_path.is_relative_to(task.project_root):
                raise ValueError("Task targets must stay within their project.")
            if not target_path.is_file():
                raise ValueError(f"Target file not found: {task.target_ref}")
        runs = []
        for task in specs:
            config = replace(self.config, project_root=task.project_root, language=task.language or self.config.language)
            for representation in ("compact", "raw"):
                report = EvaluationRunner(config).evaluate(task.target_ref, representation)
                runs.append({"task": task.name, **report})
        summaries = {}
        for representation in ("compact", "raw"):
            group = [run for run in runs if run["feedback_format"] == representation]
            repairs = sum(run["repair_attempts"] for run in group)
            successes = sum(run["repair_successes"] for run in group)
            summaries[representation] = {
                "tasks": len(group),
                "successful_tasks": sum(run["success"] for run in group),
                "repair_attempts": repairs,
                "repair_successes": successes,
                "repair_success_rate": successes / repairs if repairs else None,
                "latency_seconds": sum(run["latency_seconds"] for run in group),
                "token_usage": {
                    key: sum(run["token_usage"][key] for run in group)
                    for key in ("input_tokens", "output_tokens", "total_tokens")
                } if all(run["token_usage"] is not None for run in group) else None,
            }
        report = {"spec": str(spec_path), "summary": summaries, "runs": runs}
        report_path = self.config.artifacts_dir / "evaluations" / f"comparison-{uuid4().hex}.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report["report_path"] = str(report_path)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
