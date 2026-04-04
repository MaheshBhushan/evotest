from __future__ import annotations

from pathlib import Path

from autotestgen.adapters.python.classifier import PythonFailureClassifier
from autotestgen.adapters.python.discovery import PythonDiscovery
from autotestgen.adapters.python.generation import PythonHeuristicGenerator
from autotestgen.adapters.python.prompting import PythonPromptContextBuilder
from autotestgen.adapters.python.repair import PythonRepairEngine
from autotestgen.adapters.python.runner import PythonRunner
from autotestgen.adapters.python.validation import PythonModelOutputValidator
from autotestgen.config import HarnessConfig
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


class PythonLanguageAdapter:
    language_id = "python"
    adapter_id = "python"

    def __init__(self, config: HarnessConfig) -> None:
        self.config = config.resolved()
        self.discovery_engine = PythonDiscovery()
        self.prompt_builder = PythonPromptContextBuilder(self.config)
        self.heuristic_generator = PythonHeuristicGenerator()
        self.validator = PythonModelOutputValidator()
        self.repair_engine = PythonRepairEngine()
        self.runner = PythonRunner(self.config)
        self.classifier = PythonFailureClassifier()

    def discover(self, project_root: Path) -> ProjectInventory:
        return self.discovery_engine.discover(project_root)

    def find_target(self, target_ref: str, inventory: ProjectInventory) -> FunctionTarget | ModuleTarget | None:
        return self.discovery_engine.find_target(target_ref, inventory, project_root=self.config.project_root)

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

    def rerun_for_stability(
        self,
        target: FunctionTarget | ModuleTarget,
        test_source: str,
        test_file_path: Path,
    ) -> bool:
        return self.runner.rerun_for_stability(target, test_source, test_file_path)
