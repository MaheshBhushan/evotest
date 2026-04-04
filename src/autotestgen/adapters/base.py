from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from autotestgen.models import (
    AttemptRecord,
    ExecutionResult,
    FailureClassification,
    FunctionTarget,
    GeneratedTestArtifact,
    ModuleTarget,
    PromptContext,
    ProjectInventory,
)

if TYPE_CHECKING:
    from autotestgen.generator.model_backed import ModelBackedTestGenerator

Target = FunctionTarget | ModuleTarget


class LanguageAdapter(Protocol):
    language_id: str
    adapter_id: str

    def discover(self, project_root: Path) -> ProjectInventory: ...

    def find_target(self, target_ref: str, inventory: ProjectInventory) -> Target | None: ...

    def build_prompt_context(
        self,
        *,
        target: Target,
        inventory: ProjectInventory,
        previous_failures: list[str],
        uncovered_lines: list[int],
        strategy: str,
        prompt_version: str,
        previous_attempt: AttemptRecord | None = None,
        successful_examples: list[tuple[str, str]] | None = None,
    ) -> PromptContext: ...

    def generate_heuristic(
        self,
        *,
        target: Target,
        prompt_text: str,
        output_dir: Path,
        attempt_number: int,
        generation_mode: str = "regenerate",
        parent_attempt_number: int | None = None,
    ) -> GeneratedTestArtifact: ...

    def validate_model_output(self, test_source: str, target: Target) -> None: ...

    def repair(
        self,
        *,
        target: Target,
        previous_test_source: str,
        failure: FailureClassification,
        prompt_text: str,
        attempt_number: int,
        output_dir: Path,
        parent_attempt_number: int | None = None,
        llm_generator: ModelBackedTestGenerator | None = None,
    ) -> GeneratedTestArtifact | None: ...

    def run(
        self,
        *,
        target: Target,
        test_source: str,
        test_file_path: Path,
        attempt_number: int,
    ) -> ExecutionResult: ...

    def classify_result(self, result: ExecutionResult) -> FailureClassification | None: ...
