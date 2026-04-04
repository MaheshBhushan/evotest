from pathlib import Path

from autotestgen.config import HarnessConfig
from autotestgen.generator import ModelBackedTestGenerator
from autotestgen.models import FunctionTarget, GeneratedTestArtifact


class _FakeProvider:
    name = "fake-openai"

    def generate_text(self, prompt_text: str, *, model: str, temperature: float, max_output_tokens: int) -> str:
        return "def test_generated_case():\n    assert True\n"


class _SpyAdapter:
    language_id = "python"
    adapter_id = "python-spy"

    def __init__(self) -> None:
        self.validated: list[tuple[str, str]] = []

    def discover(self, project_root: Path):  # pragma: no cover - unused in this test
        raise NotImplementedError

    def find_target(self, target_ref: str, inventory):  # pragma: no cover - unused in this test
        raise NotImplementedError

    def build_prompt_context(self, **kwargs):  # pragma: no cover - unused in this test
        raise NotImplementedError

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
        return GeneratedTestArtifact(
            target_id=target.target_id,
            attempt_number=attempt_number,
            prompt_text=prompt_text,
            test_file_path=output_dir / "fallback.py",
            test_source="def test_fallback():\n    assert True\n",
            generation_mode=generation_mode,
            parent_attempt_number=parent_attempt_number,
            language=self.language_id,
            adapter_id=self.adapter_id,
        )

    def validate_model_output(self, test_source: str, target) -> None:
        self.validated.append((test_source, target.target_id))

    def repair(self, **kwargs):  # pragma: no cover - unused in this test
        raise NotImplementedError

    def run(self, **kwargs):  # pragma: no cover - unused in this test
        raise NotImplementedError

    def classify_result(self, result):  # pragma: no cover - unused in this test
        raise NotImplementedError


def test_model_backed_generator_delegates_validation_to_adapter(tmp_path: Path) -> None:
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        generator_mode="llm",
        openai_api_key="test-key",
    )
    adapter = _SpyAdapter()
    generator = ModelBackedTestGenerator(
        config=config,
        adapter=adapter,
        provider=_FakeProvider(),
    )
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

    artifact = generator.generate(
        target,
        '<autotestgen-metadata>{"prompt_version":"feedback-v2","strategy":"baseline","previous_failures":[],"uncovered_lines":[]}</autotestgen-metadata>',
        tmp_path,
        1,
    )

    assert adapter.validated == [("def test_generated_case():\n    assert True", "pkg/maths.py::add")]
    assert artifact.provider_name == "fake-openai"
    assert artifact.adapter_id == "python-spy"
