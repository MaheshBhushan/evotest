import ast
from pathlib import Path

from autotestgen.config import HarnessConfig
from autotestgen.generator import ModelBackedTestGenerator, RepairEngine, StaticTestGenerator
from autotestgen.models import FailureClassification, FunctionTarget


def _target(tmp_path: Path) -> FunctionTarget:
    return FunctionTarget(
        target_id="pkg/maths.py::add",
        file_path=tmp_path / "pkg" / "maths.py",
        module_name="pkg.maths",
        symbol_name="add",
        qualname="add",
        line_start=1,
        line_end=2,
        source="def add(a, b):\n    return a + b\n",
    )


class _CapturingProvider:
    name = "fake-openai"

    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate_text(self, prompt_text: str, *, model: str, temperature: float, max_output_tokens: int) -> str:
        self.prompts.append(prompt_text)
        return self.response


def test_assertion_repair_preserves_case_local_assertions(tmp_path: Path) -> None:
    repair = RepairEngine(StaticTestGenerator())
    previous = """import pytest

from pkg.maths import add

def test_add_attempt_1_case_1():
    result = add(1, 1)
    assert result == 99

def test_add_attempt_1_case_2():
    result = add(1, 2)
    assert result == 99

def test_add_attempt_1_case_3():
    result = add(1, 0)
    assert result == 99
"""
    regenerated = """import pytest

from pkg.maths import add

def test_add_attempt_2_case_1():
    result = add(1, 1)
    assert result == 2

def test_add_attempt_2_case_2():
    result = add(1, 2)
    assert result == 3

def test_add_attempt_2_case_3():
    result = add(1, 0)
    assert result == 1
"""

    repaired = repair._repair_assertions(previous, regenerated)

    assert repaired.count("assert result == 2") == 1
    assert repaired.count("assert result == 3") == 1
    assert repaired.count("assert result == 1") == 1
    assert repaired.index("assert result == 2") < repaired.index("def test_add_attempt_1_case_2")
    assert repaired.index("assert result == 3") > repaired.index("def test_add_attempt_1_case_2")
    assert repaired.index("assert result == 1") > repaired.index("def test_add_attempt_1_case_3")


def test_invocation_repair_keeps_distinct_case_bodies(tmp_path: Path) -> None:
    repair = RepairEngine(StaticTestGenerator())
    previous = """import pytest

from pkg.maths import add

def test_add_attempt_1_case_1():
    result = add(None, None)
    assert result == 99

def test_add_attempt_1_case_2():
    result = add(None, None)
    assert result == 99

def test_add_attempt_1_case_3():
    result = add(None, None)
    assert result == 99
"""
    regenerated = """import pytest

from pkg.maths import add

def test_add_attempt_2_case_1():
    result = add(1, 1)
    assert result == 2

def test_add_attempt_2_case_2():
    result = add(1, 2)
    assert result == 3

def test_add_attempt_2_case_3():
    result = add(1, 0)
    assert result == 1
"""

    repaired = repair._repair_invocation(previous, regenerated)

    assert "def test_add_attempt_2_case_1()" in repaired
    assert "def test_add_attempt_2_case_2()" in repaired
    assert "def test_add_attempt_2_case_3()" in repaired
    assert repaired.count("result = add(1, 1)") == 1
    assert repaired.count("result = add(1, 2)") == 1
    assert repaired.count("result = add(1, 0)") == 1


def test_repair_engine_regression_for_assertion_failure_uses_case_specific_asserts(tmp_path: Path) -> None:
    target = _target(tmp_path)
    repair = RepairEngine(StaticTestGenerator())
    previous = """import pytest

from pkg.maths import add

def test_add_attempt_1_case_1():
    result = add(1, 1)
    assert result == 999

def test_add_attempt_1_case_2():
    result = add(1, 2)
    assert result == 999

def test_add_attempt_1_case_3():
    result = add(1, 0)
    assert result == 999
"""
    prompt_text = (
        '<autotestgen-metadata>{"prompt_version":"feedback-v2","strategy":"assertion_repair",'
        '"previous_failures":["assertion_failure: wrong result"],"uncovered_lines":[1,2]}</autotestgen-metadata>'
    )
    failure = FailureClassification(
        category="assertion_semantics",
        subtype="assertion_failure",
        recoverable=True,
        message="wrong result",
        summary="repair assertions",
    )

    repaired = repair.repair(
        target=target,
        previous_test_source=previous,
        failure=failure,
        prompt_text=prompt_text,
        attempt_number=2,
        output_dir=tmp_path,
    )

    assert repaired is not None
    assert repaired.test_source.count("assert result == 2") == 1
    assert repaired.test_source.count("assert result == 3") == 1
    assert repaired.test_source.count("assert result == 1") == 1
    assert repaired.provider_name == "heuristic"


def test_repair_engine_uses_llm_prompt_with_failure_context(tmp_path: Path) -> None:
    target = _target(tmp_path)
    provider = _CapturingProvider(
        "def test_add_attempt_2_case_1():\n"
        "    result = add(1, 1)\n"
        "    assert result == 2\n"
    )
    llm_generator = ModelBackedTestGenerator(
        config=HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".autotestgen" / "state.db",
            generator_mode="llm",
            openai_api_key="test-key",
        ),
        provider=provider,
    )
    repair = RepairEngine(StaticTestGenerator(), llm_generator)
    previous = """import pytest

from pkg.maths import add

def test_add_attempt_1_case_1():
    result = add(None, None)
    assert result == 999
"""
    prompt_text = (
        '<autotestgen-metadata>{"prompt_version":"feedback-v2","strategy":"argument_repair",'
        '"previous_failures":["type_error: wrong args"],"uncovered_lines":[1]}</autotestgen-metadata>'
    )
    failure = FailureClassification(
        category="runtime",
        subtype="type_error",
        recoverable=True,
        message="unsupported operand type",
        summary="repair invocation",
    )

    repaired = repair.repair(
        target=target,
        previous_test_source=previous,
        failure=failure,
        prompt_text=prompt_text,
        attempt_number=2,
        output_dir=tmp_path,
        parent_attempt_number=1,
    )

    assert repaired is not None
    assert repaired.provider_name == "fake-openai"
    assert repaired.generation_mode == "repair"
    assert repaired.parent_attempt_number == 1
    assert "Failure subtype: type_error" in provider.prompts[0]
    assert "Current failing generated test:" in provider.prompts[0]
    assert "unsupported operand type" in provider.prompts[0]
    assert "Repair guidance:" in provider.prompts[0]


def test_coverage_repair_adds_new_cases_instead_of_only_rewriting_assertions(tmp_path: Path) -> None:
    repair = RepairEngine(StaticTestGenerator())
    previous = """import pytest

from pkg.chooser import choose_bucket

def test_choose_bucket_attempt_1_case_1():
    result = choose_bucket(False)
    assert result == 'disabled'
"""
    regenerated = """import pytest

from pkg.chooser import choose_bucket

def test_choose_bucket_attempt_2_case_1():
    result = choose_bucket(True)
    assert result == 'enabled'

def test_choose_bucket_attempt_2_case_2():
    result = choose_bucket(False)
    assert result == 'disabled'
"""

    repaired = repair._repair_coverage_expansion(previous, regenerated)

    assert "def test_choose_bucket_attempt_2_case_1()" in repaired
    assert "def test_choose_bucket_attempt_2_case_2()" in repaired
    assert repaired.count("assert result == 'enabled'") == 1
    assert repaired.count("assert result == 'disabled'") == 1


def test_assertion_repair_preserves_class_based_test_structure(tmp_path: Path) -> None:
    repair = RepairEngine(StaticTestGenerator())
    previous = """import pytest

from pkg.greeter import Greeter

class TestGreeter:
    def test_greet_attempt_1_case_1(self):
        subject = Greeter()
        assert subject.greet('sam') == 'wrong'
"""
    regenerated = """import pytest

from pkg.greeter import Greeter

def test_greet_attempt_2_case_1():
    subject = Greeter()
    assert subject.greet('sam') == 'hi sam'
"""

    repaired = repair._repair_assertions(previous, regenerated)

    assert "class TestGreeter:" in repaired
    assert "    def test_greet_attempt_1_case_1(self):" in repaired
    assert "assert subject.greet('sam') == 'hi sam'" in repaired


def test_invocation_repair_preserves_class_based_structure(tmp_path: Path) -> None:
    repair = RepairEngine(StaticTestGenerator())
    previous = """import pytest

from pkg.greeter import Greeter

class TestGreeter:
    def test_greet_attempt_1_case_1(self):
        subject = Greeter()
        result = subject.greet(None)
        assert result == 'wrong'
"""
    regenerated = """import pytest

from pkg.greeter import Greeter

def test_greet_attempt_2_case_1():
    subject = Greeter()
    result = subject.greet('sam')
    assert result == 'hi sam'
"""

    repaired = repair._repair_invocation(previous, regenerated)

    assert "class TestGreeter:" in repaired
    assert "    def test_greet_attempt_2_case_1(self):" in repaired
    assert "        result = subject.greet('sam')" in repaired


def test_import_repair_preserves_class_based_structure(tmp_path: Path) -> None:
    repair = RepairEngine(StaticTestGenerator())
    target = FunctionTarget(
        target_id="pkg/greeter.py::Greeter.greet",
        file_path=tmp_path / "pkg" / "greeter.py",
        module_name="pkg.greeter",
        symbol_name="greet",
        qualname="Greeter.greet",
        line_start=1,
        line_end=2,
        source="class Greeter:\n    def greet(self, name):\n        return f'hi {name}'\n",
        owner_qualname="Greeter",
        method_kind="instance",
    )
    previous = """import pytest

from pkg.greeter import Greeter

class TestGreeter:
    def test_greet_attempt_1_case_1(self):
        subject = Greeter()
        assert subject.greet('sam') == 'hi sam'
"""

    repaired = repair._repair_imports(previous, target, "def test_fallback():\n    pass\n")

    assert "class TestGreeter:" in repaired
    assert "def _load_target_owner():" in repaired
    assert "    def test_greet_attempt_1_case_1(self):" in repaired
    ast.parse(repaired)
