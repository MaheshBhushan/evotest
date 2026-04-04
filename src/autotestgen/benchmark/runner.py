from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from autotestgen.config import HarnessConfig
from autotestgen.models import AttemptRecord, OrchestrationResult
from autotestgen.orchestrator import ClosedLoopOrchestrator


@dataclass(slots=True)
class BenchmarkTargetSpec:
    name: str
    project_root: Path
    target_ref: str
    language: str | None = None


@dataclass(slots=True)
class BenchmarkTargetReport:
    name: str
    project_root: str
    target_ref: str
    language: str
    target_id: str | None
    success: bool
    stability_passed: bool
    attempts: int
    retries_to_success: int | None
    initial_coverage: float
    final_coverage: float
    coverage_gain: float
    fallback_attempts: int
    invalid_output_attempts: int
    repair_attempts: int
    repair_successes: int
    providers_used: list[str]
    repair_success_by_subtype: dict[str, dict[str, float | int]]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "project_root": self.project_root,
            "target_ref": self.target_ref,
            "language": self.language,
            "target_id": self.target_id,
            "success": self.success,
            "stability_passed": self.stability_passed,
            "attempts": self.attempts,
            "retries_to_success": self.retries_to_success,
            "initial_coverage": self.initial_coverage,
            "final_coverage": self.final_coverage,
            "coverage_gain": self.coverage_gain,
            "fallback_attempts": self.fallback_attempts,
            "invalid_output_attempts": self.invalid_output_attempts,
            "repair_attempts": self.repair_attempts,
            "repair_successes": self.repair_successes,
            "providers_used": self.providers_used,
            "repair_success_by_subtype": self.repair_success_by_subtype,
            "error": self.error,
        }


@dataclass(slots=True)
class BenchmarkSummary:
    total_targets: int
    successful_targets: int
    failed_targets: int
    pass_rate: float
    total_attempts: int
    average_final_coverage: float
    average_coverage_gain: float
    average_retries_to_success: float | None
    fallback_attempts: int
    fallback_rate: float
    invalid_output_attempts: int
    invalid_output_rate: float
    repair_attempts: int
    repair_successes: int
    repair_success_rate: float
    repair_success_by_subtype: dict[str, dict[str, float | int]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_targets": self.total_targets,
            "successful_targets": self.successful_targets,
            "failed_targets": self.failed_targets,
            "pass_rate": self.pass_rate,
            "total_attempts": self.total_attempts,
            "average_final_coverage": self.average_final_coverage,
            "average_coverage_gain": self.average_coverage_gain,
            "average_retries_to_success": self.average_retries_to_success,
            "fallback_attempts": self.fallback_attempts,
            "fallback_rate": self.fallback_rate,
            "invalid_output_attempts": self.invalid_output_attempts,
            "invalid_output_rate": self.invalid_output_rate,
            "repair_attempts": self.repair_attempts,
            "repair_successes": self.repair_successes,
            "repair_success_rate": self.repair_success_rate,
            "repair_success_by_subtype": self.repair_success_by_subtype,
        }


@dataclass(slots=True)
class BenchmarkReport:
    spec_path: str
    generated_at: str
    generator_mode: str
    model_name: str | None
    language: str
    summary: BenchmarkSummary
    targets: list[BenchmarkTargetReport]

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_path": self.spec_path,
            "generated_at": self.generated_at,
            "generator_mode": self.generator_mode,
            "model_name": self.model_name,
            "language": self.language,
            "summary": self.summary.to_dict(),
            "targets": [target.to_dict() for target in self.targets],
        }


class BenchmarkRunner:
    def __init__(self, config: HarnessConfig) -> None:
        self.config = config.resolved()

    def run(self, spec_path: Path, *, report_path: Path | None = None) -> BenchmarkReport:
        resolved_spec_path = spec_path.resolve()
        session_root = self._session_artifacts_root(resolved_spec_path)
        shutil.rmtree(session_root, ignore_errors=True)
        session_root.mkdir(parents=True, exist_ok=True)
        specs = self.load_specs(resolved_spec_path)
        target_reports = [self._run_spec(spec, session_root=session_root, index=index) for index, spec in enumerate(specs, start=1)]
        report = BenchmarkReport(
            spec_path=str(resolved_spec_path),
            generated_at=datetime.now(timezone.utc).isoformat(),
            generator_mode=self.config.generator_mode,
            model_name=self.config.model_name if self.config.generator_mode != "heuristic" else None,
            language=self._report_language(target_reports),
            summary=self._build_summary(target_reports),
            targets=target_reports,
        )
        if report_path is not None:
            resolved_report_path = report_path.resolve()
            resolved_report_path.parent.mkdir(parents=True, exist_ok=True)
            resolved_report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        return report

    def load_specs(self, spec_path: Path) -> list[BenchmarkTargetSpec]:
        payload = json.loads(spec_path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            entries = payload
            default_project_root = "."
        else:
            entries = payload.get("targets", [])
            default_project_root = payload.get("project_root", ".")
        specs: list[BenchmarkTargetSpec] = []
        for index, entry in enumerate(entries, start=1):
            project_root_value = entry.get("project_root", default_project_root)
            resolved_project_root = self._resolve_spec_path(spec_path.parent, project_root_value)
            target_ref = str(entry["target"])
            specs.append(
                BenchmarkTargetSpec(
                    name=str(entry.get("name", f"target-{index}")),
                    project_root=resolved_project_root,
                    target_ref=target_ref,
                    language=str(entry["language"]) if "language" in entry else None,
                )
            )
        return specs

    def _run_spec(self, spec: BenchmarkTargetSpec, *, session_root: Path, index: int) -> BenchmarkTargetReport:
        try:
            orchestrator = ClosedLoopOrchestrator(
                self._config_for_project(spec.project_root, spec.name, session_root, index, spec.language)
            )
            result = orchestrator.run_target(spec.target_ref)
        except Exception as exc:
            return BenchmarkTargetReport(
                name=spec.name,
                project_root=str(spec.project_root),
                target_ref=spec.target_ref,
                language=spec.language or self.config.language,
                target_id=None,
                success=False,
                stability_passed=False,
                attempts=0,
                retries_to_success=None,
                initial_coverage=0.0,
                final_coverage=0.0,
                coverage_gain=0.0,
                fallback_attempts=0,
                invalid_output_attempts=0,
                repair_attempts=0,
                repair_successes=0,
                providers_used=[],
                repair_success_by_subtype={},
                error=str(exc),
            )
        return self._target_report(spec, result)

    def _target_report(self, spec: BenchmarkTargetSpec, result: OrchestrationResult) -> BenchmarkTargetReport:
        initial_coverage = self._initial_coverage(result.attempts)
        repair_stats = self._repair_stats(result.attempts)
        repair_attempts = sum(int(bucket["attempts"]) for bucket in repair_stats.values())
        repair_successes = sum(int(bucket["successes"]) for bucket in repair_stats.values())
        return BenchmarkTargetReport(
            name=spec.name,
            project_root=str(spec.project_root),
            target_ref=spec.target_ref,
            language=result.language,
            target_id=result.target_id,
            success=result.final_success,
            stability_passed=result.stability_passed,
            attempts=len(result.attempts),
            retries_to_success=(len(result.attempts) - 1) if result.final_success and result.attempts else None,
            initial_coverage=initial_coverage,
            final_coverage=round(result.final_coverage, 2),
            coverage_gain=round(max(result.final_coverage - initial_coverage, 0.0), 2),
            fallback_attempts=sum(1 for attempt in result.attempts if attempt.fallback_reason),
            invalid_output_attempts=sum(
                1 for attempt in result.attempts if self._is_invalid_output_reason(attempt.fallback_reason)
            ),
            repair_attempts=repair_attempts,
            repair_successes=repair_successes,
            providers_used=sorted({attempt.provider_name or "heuristic" for attempt in result.attempts}),
            repair_success_by_subtype=repair_stats,
        )

    def _build_summary(self, targets: list[BenchmarkTargetReport]) -> BenchmarkSummary:
        total_targets = len(targets)
        successful_targets = sum(1 for target in targets if target.success)
        total_attempts = sum(target.attempts for target in targets)
        retries_to_success = [target.retries_to_success for target in targets if target.retries_to_success is not None]
        fallback_attempts = sum(target.fallback_attempts for target in targets)
        invalid_output_attempts = sum(target.invalid_output_attempts for target in targets)
        repair_attempts = sum(target.repair_attempts for target in targets)
        repair_successes = sum(target.repair_successes for target in targets)
        repair_by_subtype = self._merge_repair_stats(target.repair_success_by_subtype for target in targets)
        return BenchmarkSummary(
            total_targets=total_targets,
            successful_targets=successful_targets,
            failed_targets=total_targets - successful_targets,
            pass_rate=self._rate(successful_targets, total_targets),
            total_attempts=total_attempts,
            average_final_coverage=round(mean([target.final_coverage for target in targets]), 2) if targets else 0.0,
            average_coverage_gain=round(mean([target.coverage_gain for target in targets]), 2) if targets else 0.0,
            average_retries_to_success=round(mean(retries_to_success), 2) if retries_to_success else None,
            fallback_attempts=fallback_attempts,
            fallback_rate=self._rate(fallback_attempts, total_attempts),
            invalid_output_attempts=invalid_output_attempts,
            invalid_output_rate=self._rate(invalid_output_attempts, total_attempts),
            repair_attempts=repair_attempts,
            repair_successes=repair_successes,
            repair_success_rate=self._rate(repair_successes, repair_attempts),
            repair_success_by_subtype=repair_by_subtype,
        )

    def _config_for_project(
        self,
        project_root: Path,
        target_name: str,
        session_root: Path,
        index: int,
        target_language: str | None,
    ) -> HarnessConfig:
        artifacts_root = session_root / f"{index:02d}_{self._slug(target_name)}"
        return HarnessConfig(
            project_root=project_root.resolve(),
            database_path=artifacts_root / "state.db",
            artifacts_root=artifacts_root,
            language=target_language or self.config.language,
            max_iterations=self.config.max_iterations,
            max_runtime_seconds=self.config.max_runtime_seconds,
            coverage_target=self.config.coverage_target,
            pytest_command=self.config.pytest_command,
            test_output_dirname=self.config.test_output_dirname,
            stability_reruns=self.config.stability_reruns,
            generator_mode=self.config.generator_mode,
            model_name=self.config.model_name,
            temperature=self.config.temperature,
            max_output_tokens=self.config.max_output_tokens,
            openai_api_key=self.config.openai_api_key,
        )

    def _session_artifacts_root(self, spec_path: Path) -> Path:
        return self.config.artifacts_dir / "benchmarks" / self._slug(spec_path.stem)

    def _report_language(self, targets: list[BenchmarkTargetReport]) -> str:
        languages = sorted({target.language for target in targets if target.language})
        if not languages:
            return self.config.language
        if len(languages) == 1:
            return languages[0]
        return "mixed"

    @staticmethod
    def _resolve_spec_path(base_dir: Path, raw_path: str) -> Path:
        candidate = Path(raw_path)
        if candidate.is_absolute():
            return candidate.resolve()
        return (base_dir / candidate).resolve()

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
        return slug or "benchmark"

    @staticmethod
    def _initial_coverage(attempts: list[AttemptRecord]) -> float:
        if not attempts:
            return 0.0
        first_result = attempts[0].execution_result
        if first_result is None or first_result.coverage is None:
            return 0.0
        return round(first_result.coverage.coverage_percent, 2)

    @staticmethod
    def _repair_stats(attempts: list[AttemptRecord]) -> dict[str, dict[str, float | int]]:
        attempt_index = {attempt.attempt_number: attempt for attempt in attempts}
        stats: dict[str, dict[str, float | int]] = {}
        for attempt in attempts:
            if attempt.generation_mode != "repair" or attempt.parent_attempt_number is None:
                continue
            parent = attempt_index.get(attempt.parent_attempt_number)
            if parent is None or parent.failure is None:
                continue
            subtype = parent.failure.subtype
            bucket = stats.setdefault(subtype, {"attempts": 0, "successes": 0, "success_rate": 0.0})
            bucket["attempts"] = int(bucket["attempts"]) + 1
            if BenchmarkRunner._repair_fixed_failure(attempt, subtype):
                bucket["successes"] = int(bucket["successes"]) + 1
        for bucket in stats.values():
            bucket["success_rate"] = BenchmarkRunner._rate(int(bucket["successes"]), int(bucket["attempts"]))
        return stats

    @staticmethod
    def _repair_fixed_failure(attempt: AttemptRecord, prior_subtype: str) -> bool:
        if attempt.execution_result is None or not attempt.execution_result.success:
            return False
        if attempt.failure is None:
            return True
        return attempt.failure.subtype != prior_subtype

    @staticmethod
    def _merge_repair_stats(
        stats_list: Any,
    ) -> dict[str, dict[str, float | int]]:
        merged: dict[str, dict[str, float | int]] = {}
        for stats in stats_list:
            for subtype, bucket in stats.items():
                target = merged.setdefault(subtype, {"attempts": 0, "successes": 0, "success_rate": 0.0})
                target["attempts"] = int(target["attempts"]) + int(bucket["attempts"])
                target["successes"] = int(target["successes"]) + int(bucket["successes"])
        for bucket in merged.values():
            bucket["success_rate"] = BenchmarkRunner._rate(int(bucket["successes"]), int(bucket["attempts"]))
        return merged

    @staticmethod
    def _is_invalid_output_reason(reason: str | None) -> bool:
        if not reason:
            return False
        lowered = reason.lower()
        if reason.startswith("Model output"):
            return True
        return any(
            marker in lowered
            for marker in (
                "invalid syntax",
                "unexpected indent",
                "expected an indented block",
                "unindent does not match",
            )
        )

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        if denominator == 0:
            return 0.0
        return round((numerator / denominator) * 100.0, 2)
