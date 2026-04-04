from pathlib import Path
import shutil

from autotestgen.config import HarnessConfig
from autotestgen.models import (
    CoverageResult,
    ExecutionResult,
    FailureClassification,
    FunctionTarget,
    GeneratedTestArtifact,
    PromptContext,
    ProjectInventory,
)
from autotestgen.orchestrator import ClosedLoopOrchestrator


def test_orchestrator_runs_closed_loop_for_simple_function(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text(
        "def add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        max_iterations=3,
        max_runtime_seconds=15,
        coverage_target=80.0,
    )

    result = ClosedLoopOrchestrator(config).run_target("sample.py::add")

    assert result.final_success is True
    assert result.final_coverage >= 80.0
    assert result.stability_passed is True
    assert result.attempts
    assert result.attempts[0].provider_name == "heuristic"
    assert result.attempts[0].fallback_reason == "OPENAI_API_KEY is not set."


def test_orchestrator_runs_closed_loop_for_instance_method(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text(
        "class Greeter:\n"
        "    def __init__(self):\n"
        "        self.prefix = 'hi'\n\n"
        "    def greet(self, name):\n"
        "        return f'{self.prefix} {name}'\n",
        encoding="utf-8",
    )
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        max_iterations=3,
        max_runtime_seconds=15,
        coverage_target=80.0,
    )

    result = ClosedLoopOrchestrator(config).run_target("sample.py::Greeter.greet")

    assert result.final_success is True
    assert result.final_coverage >= 80.0
    assert result.stability_passed is True


class _AdapterSpy:
    language_id = "python"
    adapter_id = "adapter-spy"

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root
        self.calls: list[str] = []
        self.target = FunctionTarget(
            target_id="sample.py::add",
            file_path=project_root / "sample.py",
            module_name="sample",
            symbol_name="add",
            qualname="add",
            line_start=1,
            line_end=2,
            source="def add(a, b):\n    return a + b\n",
            language=self.language_id,
            adapter_id=self.adapter_id,
        )
        self.run_attempts: list[int] = []
        self.should_fail_first = False

    def discover(self, project_root: Path) -> ProjectInventory:
        self.calls.append("discover")
        return ProjectInventory(functions=[self.target], modules=[], language=self.language_id, adapter_id=self.adapter_id)

    def find_target(self, target_ref: str, inventory: ProjectInventory) -> FunctionTarget:
        self.calls.append("find_target")
        return self.target

    def build_prompt_context(
        self,
        *,
        target,
        inventory: ProjectInventory,
        previous_failures: list[str],
        uncovered_lines: list[int],
        strategy: str,
        prompt_version: str,
        previous_attempt=None,
        successful_examples=None,
    ) -> PromptContext:
        self.calls.append("build_prompt_context")
        return PromptContext(
            target_id=target.target_id,
            target_kind="function",
            prompt_version=prompt_version,
            strategy=strategy,
            source_code=target.source,
            imports=[],
            nearby_context="",
            existing_tests="",
            previous_failures=previous_failures,
            uncovered_lines=uncovered_lines,
            language=self.language_id,
            adapter_id=self.adapter_id,
            test_framework="pytest",
            task_description="Generate pytest tests for one Python target.",
            output_instructions="Output only valid pytest code.",
        )

    def generate_heuristic(
        self,
        *,
        target,
        prompt_text: str,
        output_dir: Path,
        attempt_number: int,
        generation_mode: str = "regenerate",
        parent_attempt_number: int | None = None,
    ) -> GeneratedTestArtifact:
        self.calls.append("generate_heuristic")
        return GeneratedTestArtifact(
            target_id=target.target_id,
            attempt_number=attempt_number,
            prompt_text=prompt_text,
            test_file_path=output_dir / f"generated_{attempt_number}.py",
            test_source="def test_generated_case():\n    assert True\n",
            generation_mode=generation_mode,
            parent_attempt_number=parent_attempt_number,
            language=self.language_id,
            adapter_id=self.adapter_id,
        )

    def validate_model_output(self, test_source: str, target) -> None:
        self.calls.append("validate_model_output")

    def repair(
        self,
        *,
        target,
        previous_test_source: str,
        failure: FailureClassification,
        prompt_text: str,
        attempt_number: int,
        output_dir: Path,
        parent_attempt_number: int | None = None,
        llm_generator=None,
    ) -> GeneratedTestArtifact | None:
        self.calls.append("repair")
        return GeneratedTestArtifact(
            target_id=target.target_id,
            attempt_number=attempt_number,
            prompt_text=prompt_text,
            test_file_path=output_dir / f"repaired_{attempt_number}.py",
            test_source="def test_repaired_case():\n    assert True\n",
            generation_mode="repair",
            parent_attempt_number=parent_attempt_number,
            language=self.language_id,
            adapter_id=self.adapter_id,
        )

    def run(
        self,
        *,
        target,
        test_source: str,
        test_file_path: Path,
        attempt_number: int,
    ) -> ExecutionResult:
        self.calls.append("run")
        self.run_attempts.append(attempt_number)
        success = True
        coverage = CoverageResult(statements=1, missing=0, coverage_percent=100.0, missing_lines=[])
        if self.should_fail_first and attempt_number == 1:
            success = False
            coverage = None
        return ExecutionResult(
            target_id=target.target_id,
            attempt_number=attempt_number,
            success=success,
            exit_code=0 if success else 1,
            stdout="",
            stderr="" if success else "AssertionError: boom",
            duration_seconds=0.01,
            coverage=coverage,
            test_file_path=test_file_path,
            language=self.language_id,
            adapter_id=self.adapter_id,
        )

    def classify_result(self, result: ExecutionResult) -> FailureClassification | None:
        self.calls.append("classify_result")
        if result.success:
            return None
        return FailureClassification(
            category="assertion_semantics",
            subtype="assertion_failure",
            recoverable=True,
            message="boom",
            summary="boom",
        )


def test_orchestrator_uses_adapter_methods(tmp_path: Path) -> None:
    adapter = _AdapterSpy(tmp_path)
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        max_iterations=2,
        generator_mode="heuristic",
    )

    result = ClosedLoopOrchestrator(config, adapter=adapter).run_target("sample.py::add")

    assert result.final_success is True
    assert "discover" in adapter.calls
    assert "find_target" in adapter.calls
    assert "build_prompt_context" in adapter.calls
    assert "generate_heuristic" in adapter.calls
    assert "run" in adapter.calls
    assert "classify_result" in adapter.calls


def test_orchestrator_delegates_repair_to_adapter(tmp_path: Path) -> None:
    adapter = _AdapterSpy(tmp_path)
    adapter.should_fail_first = True
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        max_iterations=3,
        generator_mode="heuristic",
    )

    result = ClosedLoopOrchestrator(config, adapter=adapter).run_target("sample.py::add")

    assert result.final_success is True
    assert "repair" in adapter.calls
    assert any(attempt.generation_mode == "repair" for attempt in result.attempts)


def test_orchestrator_discovers_mixed_language_inventory(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "sample.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "math.js").write_text("export function add(a, b) { return a + b; }\n", encoding="utf-8")

    inventory = ClosedLoopOrchestrator(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".autotestgen" / "state.db",
            language="auto",
            generator_mode="heuristic",
        )
    ).discover()

    assert inventory.language == "mixed"
    assert any(function.target_id == "sample.py::add" for function in inventory.functions)
    assert any(function.target_id == "src/math.js::add" for function in inventory.functions)


def test_orchestrator_routes_javascript_target_in_polyglot_repo(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        return
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "sample.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "math.js").write_text("export function add(a, b) { return a + b; }\n", encoding="utf-8")
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        language="auto",
        generator_mode="heuristic",
        max_iterations=3,
        max_runtime_seconds=15,
        coverage_target=80.0,
        stability_reruns=1,
    )

    result = ClosedLoopOrchestrator(config).run_target("src/math.js::add")

    assert result.language == "javascript"
    assert result.final_success is True
    assert result.final_coverage >= 80.0
