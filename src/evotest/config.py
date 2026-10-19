from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class HarnessConfig:
    project_root: Path
    database_path: Path
    artifacts_root: Path | None = None
    python_executable: str | None = None
    language: str = "python"
    max_iterations: int = 5
    max_runtime_seconds: int = 10
    coverage_target: float = 80.0
    pytest_command: str = "pytest"
    test_output_dirname: str = ".evotest/generated_tests"
    stability_reruns: int = 3
    generator_mode: str = "auto"
    model_name: str = "gpt-5.4"
    temperature: float = 0.2
    max_output_tokens: int = 4000
    openai_api_key: str | None = None
    mutation_testing: bool = False
    mutation_timeout_seconds: int = 60
    feedback_format: str = "default"

    @property
    def artifacts_dir(self) -> Path:
        if self.artifacts_root is not None:
            return self.artifacts_root
        return self.project_root / ".evotest"

    @property
    def test_output_dir(self) -> Path:
        target = Path(self.test_output_dirname)
        if target.is_absolute():
            return target
        if target.parts and target.parts[0] == ".evotest":
            target = Path(*target.parts[1:])
        return self.artifacts_dir / target

    def resolved(self) -> "HarnessConfig":
        return HarnessConfig(
            project_root=self.project_root.resolve(),
            database_path=self.database_path.resolve(),
            artifacts_root=self.artifacts_root.resolve() if self.artifacts_root is not None else None,
            python_executable=self.python_executable,
            language=self.language,
            max_iterations=self.max_iterations,
            max_runtime_seconds=self.max_runtime_seconds,
            coverage_target=self.coverage_target,
            pytest_command=self.pytest_command,
            test_output_dirname=self.test_output_dirname,
            stability_reruns=self.stability_reruns,
            generator_mode=self.generator_mode,
            model_name=self.model_name,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            openai_api_key=self.openai_api_key,
            mutation_testing=self.mutation_testing,
            mutation_timeout_seconds=self.mutation_timeout_seconds,
            feedback_format=self.feedback_format,
        )
