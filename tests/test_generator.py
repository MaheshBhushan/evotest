from pathlib import Path

from evotest.generator import StaticTestGenerator
from evotest.models import FunctionTarget


def test_generator_emits_valid_pytest_source(tmp_path: Path) -> None:
    generator = StaticTestGenerator()
    target = FunctionTarget(
        target_id="pkg/maths.py::add",
        file_path=tmp_path / "pkg" / "maths.py",
        module_name="pkg.maths",
        symbol_name="add",
        qualname="add",
        line_start=1,
        line_end=2,
        source="def add(a, b):\n    return a + b\n",
    )

    prompt_text = (
        '<evotest-metadata>{"prompt_version":"feedback-v2","strategy":"baseline",'
        '"previous_failures":[],"uncovered_lines":[]}</evotest-metadata>'
    )
    artifact = generator.generate(target, prompt_text, tmp_path, 1)

    assert "from pkg.maths import add" in artifact.test_source
    assert "result = add(1, 1)" in artifact.test_source
    assert "assert result == 2" in artifact.test_source


def test_generator_uses_prompt_feedback_for_import_recovery(tmp_path: Path) -> None:
    generator = StaticTestGenerator()
    target = FunctionTarget(
        target_id="src/demo/maths.py::add",
        file_path=tmp_path / "src" / "demo" / "maths.py",
        module_name="demo.maths",
        symbol_name="add",
        qualname="add",
        line_start=1,
        line_end=2,
        source="def add(a, b):\n    return a + b\n",
    )

    prompt_text = (
        '<evotest-metadata>{"prompt_version":"feedback-v2","strategy":"import_recovery",'
        '"previous_failures":["import_error: import failed"],"uncovered_lines":[]}</evotest-metadata>'
    )
    artifact = generator.generate(target, prompt_text, tmp_path, 2)

    assert "def _load_target_symbol()" in artifact.test_source
    assert "return module.add" in artifact.test_source


def test_generator_does_not_emit_tautological_broad_assertions(tmp_path: Path) -> None:
    generator = StaticTestGenerator()
    target = FunctionTarget(
        target_id="pkg/greeter.py::greet",
        file_path=tmp_path / "pkg" / "greeter.py",
        module_name="pkg.greeter",
        symbol_name="greet",
        qualname="greet",
        line_start=1,
        line_end=2,
        source="def greet(name):\n    return f'hi {name}'\n",
    )

    prompt_text = (
        '<evotest-metadata>{"prompt_version":"feedback-v2","strategy":"coverage_expansion",'
        '"previous_failures":["no_coverage_gain: retry"],"uncovered_lines":[1,2]}</evotest-metadata>'
    )
    artifact = generator.generate(target, prompt_text, tmp_path, 3)

    assert "assert result is not None or result is None" not in artifact.test_source
    assert "assert result is not None" in artifact.test_source


def test_generator_coverage_expansion_adds_branch_targeted_cases(tmp_path: Path) -> None:
    generator = StaticTestGenerator()
    target = FunctionTarget(
        target_id="pkg/chooser.py::choose_bucket",
        file_path=tmp_path / "pkg" / "chooser.py",
        module_name="pkg.chooser",
        symbol_name="choose_bucket",
        qualname="choose_bucket",
        line_start=1,
        line_end=4,
        source=(
            "def choose_bucket(flag):\n"
            "    if flag:\n"
            "        return 'enabled'\n"
            "    return 'disabled'\n"
        ),
    )

    prompt_text = (
        '<evotest-metadata>{"prompt_version":"feedback-v2","strategy":"coverage_expansion",'
        '"previous_failures":["no_coverage_gain: retry"],"uncovered_lines":[3]}</evotest-metadata>'
    )
    artifact = generator.generate(target, prompt_text, tmp_path, 4)

    assert artifact.test_source.count("def test_choose_bucket_attempt_4_case_") >= 2
    assert "result = choose_bucket(True)" in artifact.test_source
    assert "result = choose_bucket(False)" in artifact.test_source
    assert "assert result == 'enabled'" in artifact.test_source
    assert "assert result == 'disabled'" in artifact.test_source


def test_generator_supports_instance_method_targets(tmp_path: Path) -> None:
    generator = StaticTestGenerator()
    target = FunctionTarget(
        target_id="pkg/greeter.py::Greeter.greet",
        file_path=tmp_path / "pkg" / "greeter.py",
        module_name="pkg.greeter",
        symbol_name="greet",
        qualname="Greeter.greet",
        line_start=1,
        line_end=5,
        source="def greet(self, name):\n    return f'hi {name}'\n",
        owner_qualname="Greeter",
        method_kind="instance",
        constructor_parameters=[],
    )

    prompt_text = (
        '<evotest-metadata>{"prompt_version":"feedback-v2","strategy":"baseline",'
        '"previous_failures":[],"uncovered_lines":[]}</evotest-metadata>'
    )
    artifact = generator.generate(target, prompt_text, tmp_path, 5)

    assert "from pkg.greeter import Greeter" in artifact.test_source
    assert "subject = Greeter()" in artifact.test_source
    assert "result = subject.greet('sample')" in artifact.test_source
