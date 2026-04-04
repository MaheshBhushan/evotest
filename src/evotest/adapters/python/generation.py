from __future__ import annotations

from pathlib import Path

from evotest.generator.engine import StaticTestGenerator
from evotest.models import FunctionTarget, GeneratedTestArtifact, ModuleTarget


class PythonHeuristicGenerator:
    language_id = "python"
    adapter_id = "python"

    def __init__(self) -> None:
        self.generator = StaticTestGenerator()

    def generate(
        self,
        *,
        target: FunctionTarget | ModuleTarget,
        prompt_text: str,
        output_dir: Path,
        attempt_number: int,
        generation_mode: str = "regenerate",
        parent_attempt_number: int | None = None,
    ) -> GeneratedTestArtifact:
        artifact = self.generator.generate(
            target=target,
            prompt_text=prompt_text,
            output_dir=output_dir,
            attempt_number=attempt_number,
            generation_mode=generation_mode,
            parent_attempt_number=parent_attempt_number,
        )
        artifact.language = self.language_id
        artifact.adapter_id = self.adapter_id
        return artifact
