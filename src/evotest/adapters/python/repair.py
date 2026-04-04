from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from evotest.generator.engine import StaticTestGenerator
from evotest.generator.repair import RepairEngine
from evotest.models import FailureClassification, FunctionTarget, GeneratedTestArtifact, ModuleTarget

if TYPE_CHECKING:
    from evotest.generator.model_backed import ModelBackedTestGenerator


class PythonRepairEngine:
    language_id = "python"
    adapter_id = "python"

    def __init__(self) -> None:
        self.generator = StaticTestGenerator()

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
        llm_generator: ModelBackedTestGenerator | None = None,
    ) -> GeneratedTestArtifact | None:
        engine = RepairEngine(self.generator, llm_generator)
        artifact = engine.repair(
            target=target,
            previous_test_source=previous_test_source,
            failure=failure,
            prompt_text=prompt_text,
            attempt_number=attempt_number,
            output_dir=output_dir,
            parent_attempt_number=parent_attempt_number,
        )
        if artifact is not None:
            artifact.language = self.language_id
            artifact.adapter_id = self.adapter_id
        return artifact
