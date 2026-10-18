from __future__ import annotations

import difflib
import json
from pathlib import Path
from statistics import mean
from typing import Iterable

from evotest.config import HarnessConfig
from evotest.models import AttemptRecord, FunctionTarget, ModuleTarget, OrchestrationResult, ProjectInventory
from evotest.orchestrator import ClosedLoopOrchestrator

from .bootstrap import EnvironmentBootstrapper
from .detect import ProjectDetector
from .intake import RepositoryIntake
from .models import (
    BootstrapResult,
    ProjectDetection,
    RepositoryOutputPaths,
    RepositoryRunReport,
    RepositorySource,
    RepositorySummary,
    RepositoryTargetReport,
)
from .repair import RepoRepairEngine, SourceRepairOutcome


class RepositoryWorkflowRunner:
    def __init__(self, config: HarnessConfig, *, repo_repair_engine: RepoRepairEngine | None = None) -> None:
        self.config = config.resolved()
        self.intake = RepositoryIntake()
        self.detector = ProjectDetector()
        self.bootstrapper = EnvironmentBootstrapper(config)
        self.repo_repair_engine = repo_repair_engine or RepoRepairEngine(config)

    def run(
        self,
        source: str,
        *,
        branch: str | None = None,
        commit: str | None = None,
        ref: str | None = None,
        target_ref: str | None = None,
        max_targets: int = 3,
        workspace_root: Path | None = None,
        skip_bootstrap: bool = False,
        repair_repo: bool = False,
        repo_repair_attempts: int = 1,
        report_path: Path | None = None,
        review_path: Path | None = None,
        patch_path: Path | None = None,
    ) -> RepositoryRunReport:
        session_root = self._session_root(source, workspace_root)
        source_spec = RepositorySource(raw=source, branch=branch, commit=commit, ref=ref)
        clone_root = self.intake.prepare_workspace(source_spec, session_root)
        detection = self.detector.detect(clone_root, requested_language=self.config.language)
        bootstrap = self._bootstrap(clone_root, detection, skip_bootstrap=skip_bootstrap)
        harness_config = self._repo_config(clone_root, detection, bootstrap)
        orchestrator = ClosedLoopOrchestrator(harness_config)
        inventory = orchestrator.discover()
        selected_targets = self._select_targets(inventory, target_ref=target_ref, max_targets=max_targets)
        project_before = self._snapshot_tree(clone_root)
        inventory_targets = self._target_lookup(inventory)
        target_reports: list[RepositoryTargetReport] = []
        for selected_target in selected_targets:
            try:
                target = inventory_targets[selected_target]
                result, source_repairs = self._run_target_with_source_repairs(
                    orchestrator=orchestrator,
                    target=target,
                    target_ref=selected_target,
                    repair_repo=repair_repo,
                    repo_repair_attempts=repo_repair_attempts,
                )
                target_reports.append(self._target_report(harness_config, result, source_repairs))
            except Exception as exc:
                target_reports.append(
                    RepositoryTargetReport(
                        target_id=selected_target,
                        success=False,
                        stability_passed=False,
                        attempts=0,
                        retries_used=None,
                        initial_coverage=0.0,
                        final_coverage=0.0,
                        coverage_gain=0.0,
                        failure_category=None,
                        failure_subtype=None,
                        fallback_attempts=0,
                        invalid_output_attempts=0,
                        repair_attempts=0,
                        repair_successes=0,
                        source_repair_attempts=0,
                        source_repair_success=False,
                        source_repair_files=[],
                        source_repair_error=None,
                        providers_used=[],
                        generated_files=[],
                        error=str(exc),
                    )
                )
        patch_text = self._build_patch(project_before, clone_root)
        outputs = RepositoryOutputPaths(
            report_path=(report_path or (session_root / "repo_run_report.json")).resolve(),
            review_path=(review_path or (session_root / "repo_review.md")).resolve(),
            patch_path=(patch_path or (session_root / "generated_tests.patch")).resolve(),
        )
        summary = self._build_summary(target_reports)
        report = RepositoryRunReport(
            source=source,
            workspace_root=session_root,
            clone_root=clone_root,
            detection=detection,
            bootstrap=bootstrap,
            outputs=outputs,
            summary=summary,
            targets=target_reports,
        )
        outputs.report_path.parent.mkdir(parents=True, exist_ok=True)
        outputs.review_path.parent.mkdir(parents=True, exist_ok=True)
        outputs.patch_path.parent.mkdir(parents=True, exist_ok=True)
        outputs.report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        outputs.review_path.write_text(self._build_review(report), encoding="utf-8")
        outputs.patch_path.write_text(patch_text, encoding="utf-8")
        return report

    def _session_root(self, source: str, workspace_root: Path | None) -> Path:
        base_root = workspace_root or (self.config.project_root / ".evotest_repo_runs")
        return base_root.resolve() / self.intake.workspace_name(source)

    def _bootstrap(
        self,
        clone_root: Path,
        detection: ProjectDetection,
        *,
        skip_bootstrap: bool,
    ) -> BootstrapResult:
        try:
            return self.bootstrapper.bootstrap(clone_root, detection, skip_install=skip_bootstrap)
        except Exception as exc:
            return BootstrapResult(
                attempted=not skip_bootstrap,
                success=False,
                error=str(exc),
            )

    def _repo_config(
        self,
        clone_root: Path,
        detection: ProjectDetection,
        bootstrap: BootstrapResult,
    ) -> HarnessConfig:
        return HarnessConfig(
            project_root=clone_root.resolve(),
            database_path=(clone_root / ".evotest" / "state.db").resolve(),
            artifacts_root=(clone_root / ".evotest").resolve(),
            python_executable=bootstrap.python_executable,
            language="auto" if detection.language == "mixed" else detection.language,
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
            mutation_testing=self.config.mutation_testing,
            mutation_timeout_seconds=self.config.mutation_timeout_seconds,
        )

    @staticmethod
    def _select_targets(
        inventory: ProjectInventory,
        *,
        target_ref: str | None,
        max_targets: int,
    ) -> list[str]:
        if target_ref:
            return [target_ref]
        candidates: list[FunctionTarget | ModuleTarget] = [*inventory.functions, *inventory.modules]
        selected = candidates[: max(max_targets, 1)]
        return [candidate.target_id for candidate in selected]

    def _target_report(
        self,
        config: HarnessConfig,
        result: OrchestrationResult,
        source_repairs: list[SourceRepairOutcome],
    ) -> RepositoryTargetReport:
        initial_coverage = self._initial_coverage(result.attempts)
        final_failure = result.attempts[-1].failure if result.attempts else None
        generated_files = sorted(
            {
                attempt.generated_test_path.relative_to(config.project_root).as_posix()
                for attempt in result.attempts
                if attempt.generated_test_path.exists()
            }
        )
        repair_attempts = sum(1 for attempt in result.attempts if attempt.generation_mode == "repair")
        repair_successes = sum(
            1
            for attempt in result.attempts
            if attempt.generation_mode == "repair" and attempt.execution_result and attempt.execution_result.success
        )
        source_repair_files = sorted({outcome.source_file for outcome in source_repairs if outcome.source_file})
        source_repair_error = next((outcome.error for outcome in reversed(source_repairs) if outcome.error), None)
        return RepositoryTargetReport(
            target_id=result.target_id,
            success=result.final_success,
            stability_passed=result.stability_passed,
            attempts=len(result.attempts),
            retries_used=(len(result.attempts) - 1) if result.attempts else None,
            initial_coverage=initial_coverage,
            final_coverage=round(result.final_coverage, 2),
            mutation=result.mutation,
            coverage_gain=round(max(result.final_coverage - initial_coverage, 0.0), 2),
            failure_category=final_failure.category if final_failure else None,
            failure_subtype=final_failure.subtype if final_failure else None,
            fallback_attempts=sum(1 for attempt in result.attempts if attempt.fallback_reason),
            invalid_output_attempts=sum(
                1 for attempt in result.attempts if self._is_invalid_output_reason(attempt.fallback_reason)
            ),
            repair_attempts=repair_attempts,
            repair_successes=repair_successes,
            source_repair_attempts=len(source_repairs),
            source_repair_success=any(outcome.verified for outcome in source_repairs),
            source_repair_files=source_repair_files,
            source_repair_error=source_repair_error,
            providers_used=sorted({attempt.provider_name or "heuristic" for attempt in result.attempts}),
            generated_files=generated_files,
        )

    @staticmethod
    def _build_summary(target_reports: list[RepositoryTargetReport]) -> RepositorySummary:
        total_targets = len(target_reports)
        successful_targets = sum(1 for target in target_reports if target.success)
        total_attempts = sum(target.attempts for target in target_reports)
        fallback_attempts = sum(target.fallback_attempts for target in target_reports)
        invalid_output_attempts = sum(target.invalid_output_attempts for target in target_reports)
        repair_attempts = sum(target.repair_attempts for target in target_reports)
        repair_successes = sum(target.repair_successes for target in target_reports)
        source_repair_attempts = sum(target.source_repair_attempts for target in target_reports)
        source_repair_successes = sum(1 for target in target_reports if target.source_repair_success)
        mutation_scores = [
            target.mutation.score for target in target_reports
            if target.mutation and target.mutation.score is not None
        ]
        return RepositorySummary(
            total_targets=total_targets,
            successful_targets=successful_targets,
            failed_targets=total_targets - successful_targets,
            pass_rate=RepositoryWorkflowRunner._rate(successful_targets, total_targets),
            average_final_coverage=round(mean([target.final_coverage for target in target_reports]), 2)
            if target_reports
            else 0.0,
            average_coverage_gain=round(mean([target.coverage_gain for target in target_reports]), 2)
            if target_reports
            else 0.0,
            average_mutation_score=round(mean(mutation_scores), 2) if mutation_scores else None,
            total_attempts=total_attempts,
            fallback_attempts=fallback_attempts,
            invalid_output_attempts=invalid_output_attempts,
            repair_attempts=repair_attempts,
            repair_successes=repair_successes,
            repair_success_rate=RepositoryWorkflowRunner._rate(repair_successes, repair_attempts),
            source_repair_attempts=source_repair_attempts,
            source_repair_successes=source_repair_successes,
            source_repair_success_rate=RepositoryWorkflowRunner._rate(source_repair_successes, total_targets),
        )

    @staticmethod
    def _initial_coverage(attempts: list[AttemptRecord]) -> float:
        if not attempts:
            return 0.0
        first_result = attempts[0].execution_result
        if first_result is None or first_result.coverage is None:
            return 0.0
        return round(first_result.coverage.coverage_percent, 2)

    @staticmethod
    def _snapshot_tree(root: Path) -> dict[str, str]:
        if not root.exists():
            return {}
        snapshot: dict[str, str] = {}
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            relative_path = path.relative_to(root)
            relative_text = relative_path.as_posix()
            if any(
                part in {".git", "__pycache__", ".pytest_cache", "node_modules", "coverage", "vendor", "evotest_runtime"}
                for part in relative_path.parts
            ):
                continue
            if ".evotest" in relative_path.parts and not relative_text.startswith(".evotest/generated_tests/"):
                continue
            try:
                snapshot[relative_text] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
        return snapshot

    @staticmethod
    def _build_patch(before: dict[str, str], after_root: Path) -> str:
        after = RepositoryWorkflowRunner._snapshot_tree(after_root)
        patch_chunks: list[str] = []
        for relative_path in sorted(set(before) | set(after)):
            old_text = before.get(relative_path, "")
            new_text = after.get(relative_path, "")
            if old_text == new_text:
                continue
            diff = difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{relative_path}",
                tofile=f"b/{relative_path}",
            )
            patch_chunks.append("".join(diff))
        return "".join(patch_chunks)

    @staticmethod
    def _build_review(report: RepositoryRunReport) -> str:
        lines = [
            "# EvoTest Review",
            "",
            f"- Source: `{report.source}`",
            f"- Workspace: `{report.clone_root}`",
            f"- Language: `{report.detection.language}`",
            f"- Framework: `{report.detection.framework}`",
            f"- Package manager: `{report.detection.package_manager}`",
            f"- Bootstrap: `{report.detection.bootstrap_method}`",
            f"- Pass rate: `{report.summary.pass_rate:.2f}%`",
            f"- Average final coverage: `{report.summary.average_final_coverage:.2f}%`",
            "- Average mutation score: `"
            + (f"{report.summary.average_mutation_score:.2f}%" if report.summary.average_mutation_score is not None else "N/A")
            + "`",
            f"- Average coverage gain: `{report.summary.average_coverage_gain:.2f}%`",
            f"- Source repair success rate: `{report.summary.source_repair_success_rate:.2f}%`",
            "",
            "## Targets",
            "",
        ]
        for target in report.targets:
            lines.extend(
                [
                    f"### `{target.target_id}`",
                    f"- Success: `{target.success}`",
                    f"- Coverage: `{target.final_coverage:.2f}%`",
                    f"- Mutation score: `{target.mutation.display() if target.mutation else 'N/A (not run)'}`",
                    f"- Mutation evidence: `{target.mutation.evidence_dir if target.mutation else 'none'}`",
                    f"- Attempts: `{target.attempts}`",
                    f"- Failure: `{target.failure_subtype or 'none'}`",
                    f"- Source repair: `{target.source_repair_success}` after `{target.source_repair_attempts}` attempt(s)",
                    f"- Source repair files: {', '.join(f'`{path}`' for path in target.source_repair_files) or '`none`'}",
                    f"- Generated files: {', '.join(f'`{path}`' for path in target.generated_files) or '`none`'}",
                    "",
                ]
            )
        return "\n".join(lines)

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

    def _run_target_with_source_repairs(
        self,
        *,
        orchestrator: ClosedLoopOrchestrator,
        target: FunctionTarget | ModuleTarget,
        target_ref: str,
        repair_repo: bool,
        repo_repair_attempts: int,
    ) -> tuple[OrchestrationResult, list[SourceRepairOutcome]]:
        result = orchestrator.run_target(target_ref)
        source_repairs: list[SourceRepairOutcome] = []
        if not repair_repo:
            return result, source_repairs
        adapter = orchestrator._adapter_for_target(target)
        for repair_number in range(1, max(repo_repair_attempts, 0) + 1):
            if result.final_success or not result.attempts:
                break
            last_attempt = result.attempts[-1]
            outcome = self.repo_repair_engine.attempt(
                target=target,
                previous_attempt=last_attempt,
                adapter=adapter,
                repair_number=repair_number,
            )
            source_repairs.append(outcome)
            if not outcome.verified:
                break
            result = orchestrator.run_target(target_ref)
        return result, source_repairs

    @staticmethod
    def _target_lookup(inventory: ProjectInventory) -> dict[str, FunctionTarget | ModuleTarget]:
        mapping: dict[str, FunctionTarget | ModuleTarget] = {}
        for target in [*inventory.functions, *inventory.modules]:
            mapping[target.target_id] = target
        return mapping
