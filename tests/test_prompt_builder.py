from autotestgen.adapters.python.prompting import PythonPromptContextBuilder
from autotestgen.config import HarnessConfig
from autotestgen.generator.engine import PromptBuilder
from autotestgen.models import (
    AttemptRecord,
    CoverageResult,
    ExecutionResult,
    FailureClassification,
    FunctionTarget,
    PromptContext,
    ProjectInventory,
)


def test_prompt_builder_emits_structured_schema_and_guidance() -> None:
    prompt = PromptBuilder().build(
        PromptContext(
            target_id="pkg/maths.py::add",
            target_kind="function",
            prompt_version="feedback-v3",
            strategy="coverage_expansion",
            source_code="def add(a, b):\n    return a + b\n",
            imports=["0:pkg.maths:add"],
            nearby_context="helper score=1.0",
            existing_tests="",
            previous_failures=["no_coverage_gain: previous case only hit the happy path"],
            uncovered_lines=[2, 3],
            language="python",
            adapter_id="python",
            test_framework="pytest",
            task_description="Generate one strong pytest test file for the requested Python target.",
            output_instructions="Output only valid pytest code and nothing else.",
            target_contract=["Exercise `pkg/maths.py::add` directly."],
            execution_constraints=["Keep side effects local and deterministic."],
            execution_evidence=["Previous generated test path: test_add_attempt_1.py"],
            reference_examples=["# prior success from other.py::helper\ndef test_helper():\n    assert helper(1) == 2"],
            output_contract=["Reference the requested target in every generated case."],
        )
    )

    assert "Target Contract:" in prompt
    assert "Execution Constraints:" in prompt
    assert "Known Failures:" in prompt
    assert "Coverage Goals:" in prompt
    assert "Reference Examples:" in prompt
    assert "Output Contract:" in prompt
    assert "What to do differently:" in prompt
    assert "no_coverage_gain: previous case only hit the happy path." in prompt
    assert "Add distinct new cases that hit uncovered branches" in prompt


def test_python_prompt_context_collects_execution_evidence_and_examples(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "from sample import add\n\n"
        "def test_add_case():\n"
        "    assert add(1, 1) == 2\n",
        encoding="utf-8",
    )
    target = FunctionTarget(
        target_id="sample.py::add",
        file_path=tmp_path / "sample.py",
        module_name="sample",
        symbol_name="add",
        qualname="add",
        line_start=1,
        line_end=2,
        source="def add(a, b):\n    return a + b\n",
    )
    previous_attempt = AttemptRecord(
        target_id=target.target_id,
        attempt_number=1,
        prompt_text="prompt",
        generated_test_path=tmp_path / ".autotestgen" / "generated_1.py",
        generated_test_source="def test_generated_case():\n    assert add(1, 1) == 2\n",
        execution_result=ExecutionResult(
            target_id=target.target_id,
            attempt_number=1,
            success=False,
            exit_code=1,
            stdout="",
            stderr="AssertionError: wrong result",
            duration_seconds=0.01,
            coverage=CoverageResult(statements=2, missing=1, coverage_percent=50.0, missing_lines=[2]),
            test_file_path=tmp_path / ".autotestgen" / "generated_1.py",
        ),
        failure=FailureClassification(
            category="assertion_semantics",
            subtype="assertion_failure",
            recoverable=True,
            message="wrong result",
            summary="expected 2, got 3",
        ),
    )
    builder = PythonPromptContextBuilder(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".autotestgen" / "state.db",
            language="python",
        )
    )

    context = builder.build(
        target=target,
        inventory=ProjectInventory(functions=[target], modules=[]),
        previous_failures=["assertion_failure: wrong result"],
        uncovered_lines=[2],
        strategy="assertion_repair",
        prompt_version="feedback-v3",
        previous_attempt=previous_attempt,
        successful_examples=[("other.py::helper", "def test_helper_case():\n    assert helper(1) == 2\n")],
    )

    assert any("Previous generated test path:" in line and "generated_1.py" in line for line in context.execution_evidence)
    assert any("Previous failure: assertion_failure -> wrong result" in line for line in context.execution_evidence)
    assert any("Previous coverage: 50.00%" in line for line in context.execution_evidence)
    assert len(context.reference_examples) == 2
    assert "prior success from other.py::helper" in context.reference_examples[0]
    assert "test_sample.py" in context.reference_examples[1]
