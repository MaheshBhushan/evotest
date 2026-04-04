from __future__ import annotations

from pathlib import Path

from evotest.config import HarnessConfig
from evotest.models import ExecutionResult, FunctionTarget, ModuleTarget
from evotest.runner.pytest_runner import PytestRunner


class PythonRunner:
    language_id = "python"
    adapter_id = "python"

    def __init__(self, config: HarnessConfig) -> None:
        self.runner = PytestRunner(config)

    def run(
        self,
        *,
        target: FunctionTarget | ModuleTarget,
        test_source: str,
        test_file_path: Path,
        attempt_number: int,
    ) -> ExecutionResult:
        result = self.runner.run(target, test_source, test_file_path, attempt_number)
        result.language = self.language_id
        result.adapter_id = self.adapter_id
        return result

    def rerun_for_stability(
        self,
        target: FunctionTarget | ModuleTarget,
        test_source: str,
        test_file_path: Path,
    ) -> bool:
        return self.runner.rerun_for_stability(target, test_source, test_file_path)
