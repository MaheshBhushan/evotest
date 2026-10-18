from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evotest.models import MutationResult


@dataclass(slots=True)
class RepositorySource:
    raw: str
    branch: str | None = None
    commit: str | None = None
    ref: str | None = None


@dataclass(slots=True)
class ProjectDetection:
    language: str
    framework: str
    package_manager: str
    bootstrap_method: str
    languages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "framework": self.framework,
            "package_manager": self.package_manager,
            "bootstrap_method": self.bootstrap_method,
            "languages": self.languages,
        }


@dataclass(slots=True)
class BootstrapResult:
    attempted: bool
    success: bool
    commands: list[str] = field(default_factory=list)
    python_executable: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "success": self.success,
            "commands": self.commands,
            "python_executable": self.python_executable,
            "error": self.error,
        }


@dataclass(slots=True)
class RepositoryTargetReport:
    target_id: str
    success: bool
    stability_passed: bool
    attempts: int
    retries_used: int | None
    initial_coverage: float
    final_coverage: float
    coverage_gain: float
    failure_category: str | None
    failure_subtype: str | None
    fallback_attempts: int
    invalid_output_attempts: int
    repair_attempts: int
    repair_successes: int
    source_repair_attempts: int
    source_repair_success: bool
    source_repair_files: list[str]
    source_repair_error: str | None
    providers_used: list[str]
    generated_files: list[str]
    error: str | None = None
    mutation: MutationResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "success": self.success,
            "stability_passed": self.stability_passed,
            "attempts": self.attempts,
            "retries_used": self.retries_used,
            "initial_coverage": self.initial_coverage,
            "final_coverage": self.final_coverage,
            "mutation": self.mutation.to_dict() if self.mutation else None,
            "coverage_gain": self.coverage_gain,
            "failure_category": self.failure_category,
            "failure_subtype": self.failure_subtype,
            "fallback_attempts": self.fallback_attempts,
            "invalid_output_attempts": self.invalid_output_attempts,
            "repair_attempts": self.repair_attempts,
            "repair_successes": self.repair_successes,
            "source_repair_attempts": self.source_repair_attempts,
            "source_repair_success": self.source_repair_success,
            "source_repair_files": self.source_repair_files,
            "source_repair_error": self.source_repair_error,
            "providers_used": self.providers_used,
            "generated_files": self.generated_files,
            "error": self.error,
        }


@dataclass(slots=True)
class RepositorySummary:
    total_targets: int
    successful_targets: int
    failed_targets: int
    pass_rate: float
    average_final_coverage: float
    average_coverage_gain: float
    total_attempts: int
    fallback_attempts: int
    invalid_output_attempts: int
    repair_attempts: int
    repair_successes: int
    repair_success_rate: float
    source_repair_attempts: int
    source_repair_successes: int
    source_repair_success_rate: float
    average_mutation_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_targets": self.total_targets,
            "successful_targets": self.successful_targets,
            "failed_targets": self.failed_targets,
            "pass_rate": self.pass_rate,
            "average_final_coverage": self.average_final_coverage,
            "average_mutation_score": self.average_mutation_score,
            "average_coverage_gain": self.average_coverage_gain,
            "total_attempts": self.total_attempts,
            "fallback_attempts": self.fallback_attempts,
            "invalid_output_attempts": self.invalid_output_attempts,
            "repair_attempts": self.repair_attempts,
            "repair_successes": self.repair_successes,
            "repair_success_rate": self.repair_success_rate,
            "source_repair_attempts": self.source_repair_attempts,
            "source_repair_successes": self.source_repair_successes,
            "source_repair_success_rate": self.source_repair_success_rate,
        }


@dataclass(slots=True)
class RepositoryOutputPaths:
    report_path: Path
    review_path: Path
    patch_path: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "report_path": str(self.report_path),
            "review_path": str(self.review_path),
            "patch_path": str(self.patch_path),
        }


@dataclass(slots=True)
class RepositoryRunReport:
    source: str
    workspace_root: Path
    clone_root: Path
    detection: ProjectDetection
    bootstrap: BootstrapResult
    outputs: RepositoryOutputPaths
    summary: RepositorySummary
    targets: list[RepositoryTargetReport]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "workspace_root": str(self.workspace_root),
            "clone_root": str(self.clone_root),
            "detection": self.detection.to_dict(),
            "bootstrap": self.bootstrap.to_dict(),
            "outputs": self.outputs.to_dict(),
            "summary": self.summary.to_dict(),
            "targets": [target.to_dict() for target in self.targets],
        }
