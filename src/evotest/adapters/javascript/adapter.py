from __future__ import annotations

from pathlib import Path

from evotest.adapters.javascript.classifier import JavaScriptFailureClassifier
from evotest.adapters.javascript.discovery import JavaScriptProjectAnalyzer
from evotest.adapters.javascript.generation import JavaScriptHeuristicGenerator
from evotest.adapters.javascript.prompting import JavaScriptPromptContextBuilder
from evotest.adapters.javascript.repair import JavaScriptRepairEngine
from evotest.adapters.javascript.runner import JavaScriptRunner
from evotest.adapters.javascript.validation import JavaScriptModelOutputValidator
from evotest.config import HarnessConfig
from evotest.models import (
    AttemptRecord,
    ExecutionResult,
    FailureClassification,
    FunctionTarget,
    GeneratedTestArtifact,
    ModuleTarget,
    PromptContext,
    ProjectInventory,
)


class JavaScriptLanguageAdapter:
    language_id = "javascript"
    adapter_id = "javascript"

    def __init__(self, config: HarnessConfig) -> None:
        self.config = config.resolved()
        self.prompt_builder = JavaScriptPromptContextBuilder(self.config)
        self.heuristic_generator = JavaScriptHeuristicGenerator(self.config.project_root)
        self.validator = JavaScriptModelOutputValidator()
        self.repair_engine = JavaScriptRepairEngine(self.config.project_root)
        self.runner = JavaScriptRunner(self.config)
        self.classifier = JavaScriptFailureClassifier()

    def discover(self, project_root: Path) -> ProjectInventory:
        return JavaScriptProjectAnalyzer(project_root).discover()

    def find_target(self, target_ref: str, inventory: ProjectInventory) -> FunctionTarget | ModuleTarget | None:
        return JavaScriptProjectAnalyzer(self.config.project_root).find_target(target_ref, inventory)

    def build_prompt_context(
        self,
        *,
        target: FunctionTarget | ModuleTarget,
        inventory: ProjectInventory,
        previous_failures: list[str],
        uncovered_lines: list[int],
        strategy: str,
        prompt_version: str,
        previous_attempt: AttemptRecord | None = None,
        successful_examples: list[tuple[str, str]] | None = None,
    ) -> PromptContext:
        return self.prompt_builder.build(
            target=target,
            inventory=inventory,
            previous_failures=previous_failures,
            uncovered_lines=uncovered_lines,
            strategy=strategy,
            prompt_version=prompt_version,
            previous_attempt=previous_attempt,
            successful_examples=successful_examples,
        )

    def generate_heuristic(
        self,
        *,
        target: FunctionTarget | ModuleTarget,
        prompt_text: str,
        output_dir: Path,
        attempt_number: int,
        generation_mode: str = "regenerate",
        parent_attempt_number: int | None = None,
    ) -> GeneratedTestArtifact:
        return self.heuristic_generator.generate(
            target=target,
            prompt_text=prompt_text,
            output_dir=output_dir,
            attempt_number=attempt_number,
            generation_mode=generation_mode,
            parent_attempt_number=parent_attempt_number,
        )

    def validate_model_output(self, test_source: str, target: FunctionTarget | ModuleTarget) -> None:
        self.validator.validate(test_source, target)

    def repair(
        self,
        *,
        target: FunctionTarget | ModuleTarget,
        previous_test_source: str,
        failure: FailureClassification,
        prompt_text: str,
        attempt_number: int,
        output_dir: Path,
        parent_attempt_number: int | None = None,
        llm_generator=None,
    ) -> GeneratedTestArtifact | None:
        return self.repair_engine.repair(
            target=target,
            previous_test_source=previous_test_source,
            failure=failure,
            prompt_text=prompt_text,
            attempt_number=attempt_number,
            output_dir=output_dir,
            parent_attempt_number=parent_attempt_number,
            llm_generator=llm_generator,
        )

    def run(
        self,
        *,
        target: FunctionTarget | ModuleTarget,
        test_source: str,
        test_file_path: Path,
        attempt_number: int,
    ) -> ExecutionResult:
        return self.runner.run(
            target=target,
            test_source=test_source,
            test_file_path=test_file_path,
            attempt_number=attempt_number,
        )

    def classify_result(self, result: ExecutionResult) -> FailureClassification | None:
        return self.classifier.classify(result)
