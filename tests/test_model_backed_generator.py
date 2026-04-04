from pathlib import Path

from autotestgen.config import HarnessConfig
from autotestgen.generator import ModelBackedTestGenerator
from autotestgen.models import FunctionTarget


class _FakeProvider:
    name = "fake-openai"

    def __init__(self, response: str | Exception) -> None:
        self.response = response

    def generate_text(
        self,
        prompt_text: str,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        instructions: str | None = None,
    ) -> str:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


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


def _method_target(tmp_path: Path) -> FunctionTarget:
    return FunctionTarget(
        target_id="pkg/greeter.py::Greeter.greet",
        file_path=tmp_path / "pkg" / "greeter.py",
        module_name="pkg.greeter",
        symbol_name="greet",
        qualname="Greeter.greet",
        line_start=1,
        line_end=2,
        source="def greet(self, name):\n    return f'hi {name}'\n",
        owner_qualname="Greeter",
        method_kind="instance",
    )


def _prompt(strategy: str = "baseline") -> str:
    return (
        f'<autotestgen-metadata>{{"prompt_version":"feedback-v2","strategy":"{strategy}",'
        '"previous_failures":[],"uncovered_lines":[]}}</autotestgen-metadata>'
    )


def test_model_backed_generator_uses_provider_output(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "```python\n"
            "def test_generated_case():\n"
            "    value = add(1, 1)\n"
            "    assert value == 2\n"
            "```"
        ),
    )

    artifact = generator.generate(_target(tmp_path), _prompt(), tmp_path, 1)

    assert artifact.provider_name == "fake-openai"
    assert artifact.model_name == "gpt-5.4"
    assert artifact.fallback_reason is None
    assert "```" not in artifact.test_source
    assert "def test_generated_case()" in artifact.test_source


def test_model_backed_generator_falls_back_without_api_key(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="auto",
        openai_api_key=None,
    )
    generator = ModelBackedTestGenerator(
        config=config,
    )

    artifact = generator.generate(_target(tmp_path), _prompt(), tmp_path, 1)

    assert artifact.provider_name == "heuristic"
    assert artifact.fallback_reason == "OPENAI_API_KEY is not set."
    assert "def test_add_attempt_1_case_1()" in artifact.test_source


def test_model_backed_generator_falls_back_on_invalid_output(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider("value = 1"),
    )

    artifact = generator.generate(_target(tmp_path), _prompt(), tmp_path, 2)

    assert artifact.provider_name == "heuristic"
    assert artifact.fallback_reason == "Model output did not define any pytest test functions."
    assert "def test_add_attempt_2_case_1()" in artifact.test_source


def test_model_backed_generator_rejects_weak_assertions(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "def test_generated_case():\n"
            "    result = add(1, 1)\n"
            "    assert result is not None\n"
        ),
    )

    artifact = generator.generate(_target(tmp_path), _prompt(), tmp_path, 3)

    assert artifact.provider_name == "heuristic"
    assert artifact.fallback_reason == "Model output test test_generated_case only contained weak assertions."


def test_model_backed_generator_rejects_forbidden_side_effects(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "import requests\n\n"
            "def test_generated_case():\n"
            "    assert requests.get('https://example.com').status_code == 200\n"
        ),
    )

    artifact = generator.generate(_target(tmp_path), _prompt(), tmp_path, 4)

    assert artifact.provider_name == "heuristic"
    assert artifact.fallback_reason == "Model output used forbidden network or process side effects."


def test_model_backed_generator_rejects_duplicate_test_names(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "def test_dup():\n"
            "    assert add(1, 1) == 2\n\n"
            "def test_dup():\n"
            "    assert add(1, 2) == 3\n"
        ),
    )

    artifact = generator.generate(_target(tmp_path), _prompt(), tmp_path, 5)

    assert artifact.provider_name == "heuristic"
    assert artifact.fallback_reason == "Model output defined duplicate pytest test names."


def test_model_backed_generator_rejects_missing_target_reference(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "def test_generated_case():\n"
            "    value = 2 + 2\n"
            "    assert value == 4\n"
        ),
    )

    artifact = generator.generate(_target(tmp_path), _prompt(), tmp_path, 6)

    assert artifact.provider_name == "heuristic"
    assert artifact.fallback_reason == "Model output did not reference the requested target."


def test_model_backed_generator_rejects_string_only_target_reference(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "def test_generated_case():\n"
            '    label = "add"\n'
            "    value = 2 + 2\n"
            "    assert value == 4\n"
        ),
    )

    artifact = generator.generate(_target(tmp_path), _prompt(), tmp_path, 7)

    assert artifact.provider_name == "heuristic"
    assert artifact.fallback_reason == "Model output did not reference the requested target."


def test_model_backed_generator_accepts_aliased_target_import(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "from pkg.maths import add as subject\n\n"
            "def test_generated_case():\n"
            "    assert subject(1, 1) == 2\n"
        ),
    )

    artifact = generator.generate(_target(tmp_path), _prompt(), tmp_path, 7)

    assert artifact.provider_name == "fake-openai"
    assert artifact.fallback_reason is None
    assert "subject(1, 1)" in artifact.test_source


def test_model_backed_generator_rejects_direct_os_system_import(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "from pkg.maths import add\n"
            "from os import system as shell\n\n"
            "def test_generated_case():\n"
            "    shell('echo unsafe')\n"
            "    assert add(1, 1) == 2\n"
        ),
    )

    artifact = generator.generate(_target(tmp_path), _prompt(), tmp_path, 8)

    assert artifact.provider_name == "heuristic"
    assert artifact.fallback_reason == "Model output used forbidden network or process side effects."


def test_model_backed_generator_accepts_class_based_pytest_output(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "from pkg.maths import add\n\n"
            "class TestAdd:\n"
            "    def test_generated_case(self):\n"
            "        assert add(1, 1) == 2\n"
        ),
    )

    artifact = generator.generate(_target(tmp_path), _prompt(), tmp_path, 9)

    assert artifact.provider_name == "fake-openai"
    assert artifact.fallback_reason is None
    assert "class TestAdd" in artifact.test_source


def test_model_backed_generator_rejects_method_outputs_that_only_reference_owner_class(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "from pkg.greeter import Greeter\n\n"
            "def test_generated_case():\n"
            "    subject = Greeter()\n"
            "    assert isinstance(subject, Greeter)\n"
        ),
    )

    artifact = generator.generate(_method_target(tmp_path), _prompt(), tmp_path, 10)

    assert artifact.provider_name == "heuristic"
    assert artifact.fallback_reason == "Model output did not reference the requested target."


def test_model_backed_generator_rejects_method_outputs_that_only_use_hasattr(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "from pkg.greeter import Greeter\n\n"
            "def test_generated_case():\n"
            "    assert hasattr(Greeter, 'greet')\n"
        ),
    )

    artifact = generator.generate(_method_target(tmp_path), _prompt(), tmp_path, 11)

    assert artifact.provider_name == "heuristic"
    assert artifact.fallback_reason == "Model output did not reference the requested target."


def test_model_backed_generator_accepts_method_outputs_that_call_target_method(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "from pkg.greeter import Greeter\n\n"
            "def test_generated_case():\n"
            "    subject = Greeter()\n"
            "    assert subject.greet('sam') == 'hi sam'\n"
        ),
    )

    artifact = generator.generate(_method_target(tmp_path), _prompt(), tmp_path, 12)

    assert artifact.provider_name == "fake-openai"
    assert artifact.fallback_reason is None


def test_model_backed_generator_rejects_method_outputs_that_call_wrong_object(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    generator = ModelBackedTestGenerator(
        config=config,
        provider=_FakeProvider(
            "class Fake:\n"
            "    def greet(self, name):\n"
            "        return name\n\n"
            "def test_generated_case():\n"
            "    assert Fake().greet('sam') == 'sam'\n"
        ),
    )

    artifact = generator.generate(_method_target(tmp_path), _prompt(), tmp_path, 13)

    assert artifact.provider_name == "heuristic"
    assert artifact.fallback_reason == "Model output did not reference the requested target."
