from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ImportRef:
    module: str | None
    names: list[str]
    level: int = 0


@dataclass(slots=True)
class FunctionTarget:
    target_id: str
    file_path: Path
    module_name: str
    symbol_name: str
    qualname: str
    line_start: int
    line_end: int
    source: str
    imports: list[ImportRef] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    is_public: bool = True
    dependency_hints: list[str] = field(default_factory=list)
    complexity_score: int = 0
    io_hint: bool = False
    target_score: float = 0.0
    language: str = "python"
    adapter_id: str = "python"
    owner_qualname: str | None = None
    method_kind: str = "function"
    constructor_parameters: list[str] = field(default_factory=list)

    @property
    def is_method(self) -> bool:
        return self.method_kind != "function"

    @property
    def owner_root_name(self) -> str | None:
        if not self.owner_qualname:
            return None
        return self.owner_qualname.split(".", 1)[0]


@dataclass(slots=True)
class ModuleTarget:
    target_id: str
    file_path: Path
    module_name: str
    source: str
    imports: list[ImportRef] = field(default_factory=list)
    public_symbols: list[str] = field(default_factory=list)
    complexity_score: int = 0
    io_hint: bool = False
    target_score: float = 0.0
    language: str = "python"
    adapter_id: str = "python"


@dataclass(slots=True)
class ProjectInventory:
    functions: list[FunctionTarget]
    modules: list[ModuleTarget]
    language: str = "python"
    adapter_id: str = "python"


@dataclass(slots=True)
class PromptContext:
    target_id: str
    target_kind: str
    prompt_version: str
    strategy: str
    source_code: str
    imports: list[str]
    nearby_context: str
    existing_tests: str
    previous_failures: list[str]
    uncovered_lines: list[int]
    language: str = "python"
    adapter_id: str = "python"
    test_framework: str = "pytest"
    task_description: str = "Generate tests for one target."
    output_instructions: str = "Output only valid test code."
    target_contract: list[str] = field(default_factory=list)
    execution_constraints: list[str] = field(default_factory=list)
    execution_evidence: list[str] = field(default_factory=list)
    reference_examples: list[str] = field(default_factory=list)
    output_contract: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GeneratedTestArtifact:
    target_id: str
    attempt_number: int
    prompt_text: str
    test_file_path: Path
    test_source: str
    prompt_version: str = "feedback-v3"
    strategy: str = "baseline"
    generation_mode: str = "regenerate"
    parent_attempt_number: int | None = None
    provider_name: str | None = None
    model_name: str | None = None
    fallback_reason: str | None = None
    language: str = "python"
    adapter_id: str = "python"


@dataclass(slots=True)
class CoverageResult:
    statements: int
    missing: int
    coverage_percent: float
    missing_lines: list[int] = field(default_factory=list)


@dataclass(slots=True)
class MutationResult:
    status: str
    score: float | None = None
    counts: dict[str, int] = field(default_factory=dict)
    evidence_dir: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def display(self) -> str:
        if self.score is not None:
            return f"{self.score:.2f}%"
        reason = "no mutants" if self.status == "completed" else self.error or self.status
        return f"N/A ({reason})"


@dataclass(slots=True)
class ExecutionResult:
    target_id: str
    attempt_number: int
    success: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    coverage: CoverageResult | None
    test_file_path: Path
    language: str = "python"
    adapter_id: str = "python"
    mutation: MutationResult | None = None


@dataclass(slots=True)
class FailureClassification:
    category: str
    subtype: str
    recoverable: bool
    message: str
    summary: str


@dataclass(slots=True)
class AttemptRecord:
    target_id: str
    attempt_number: int
    prompt_text: str
    generated_test_path: Path
    generated_test_source: str
    prompt_version: str = "feedback-v3"
    strategy: str = "baseline"
    generation_mode: str = "regenerate"
    parent_attempt_number: int | None = None
    targeted_uncovered_lines: list[int] = field(default_factory=list)
    provider_name: str | None = None
    model_name: str | None = None
    fallback_reason: str | None = None
    language: str = "python"
    adapter_id: str = "python"
    execution_result: ExecutionResult | None = None
    failure: FailureClassification | None = None


@dataclass(slots=True)
class OrchestrationResult:
    target_id: str
    attempts: list[AttemptRecord]
    final_success: bool
    final_coverage: float
    stability_passed: bool
    language: str = "python"
    adapter_id: str = "python"

    @property
    def mutation(self) -> MutationResult | None:
        if self.attempts and self.attempts[-1].execution_result:
            return self.attempts[-1].execution_result.mutation
        return None
