import os
import shutil
from pathlib import Path

from evotest.adapters.go import GoLanguageAdapter
from evotest.adapters.go.generation import GoHeuristicGenerator
from evotest.adapters.go.repair import GoRepairEngine
from evotest.adapters.go.classifier import GoFailureClassifier
from evotest.adapters.go.runner import GoRunner
from evotest.config import HarnessConfig
from evotest.models import ExecutionResult, FailureClassification, GeneratedTestArtifact


def test_go_adapter_discovers_and_generates_heuristic_tests(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "math.go").write_text(
        "package pkg\n\n"
        "func Add(a int, b int) int {\n"
        "    return a + b\n"
        "}\n",
        encoding="utf-8",
    )
    adapter = GoLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="go",
            generator_mode="heuristic",
        )
    )

    inventory = adapter.discover(tmp_path)
    target = adapter.find_target("pkg/math.go::Add", inventory)
    assert target is not None

    context = adapter.build_prompt_context(
        target=target,
        inventory=inventory,
        previous_failures=[],
        uncovered_lines=[],
        strategy="baseline",
        prompt_version="feedback-v2",
    )
    artifact = adapter.generate_heuristic(
        target=target,
        prompt_text='<evotest-metadata>{"prompt_version":"feedback-v2","strategy":"baseline"}</evotest-metadata>',
        output_dir=tmp_path,
        attempt_number=1,
    )

    assert context.language == "go"
    assert artifact.language == "go"
    assert '"testing"' in artifact.test_source
    assert 'targetpkg "example.com/demo/pkg"' in artifact.test_source
    assert "if got != 2 {" in artifact.test_source


def test_go_validator_accepts_imported_target_reference(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "math.go").write_text(
        "package pkg\n\n"
        "func Add(a int, b int) int {\n"
        "    return a + b\n"
        "}\n",
        encoding="utf-8",
    )
    adapter = GoLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="go",
            generator_mode="heuristic",
        )
    )
    target = adapter.find_target("pkg/math.go::Add", adapter.discover(tmp_path))
    assert target is not None

    adapter.validate_model_output(
        "package generatedtests\n\n"
        "import (\n"
        '\t"testing"\n'
        '\tsubject "example.com/demo/pkg"\n'
        ")\n\n"
        "func TestGenerated(t *testing.T) {\n"
        "\tgot := subject.Add(1, 1)\n"
        "\tif got != 2 {\n"
        '\t\tt.Fatalf("expected 2, got %v", got)\n'
        "\t}\n"
        "}\n",
        target,
    )


def test_go_validator_rejects_wrong_package_reference(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "math.go").write_text(
        "package pkg\n\n"
        "func Add(a int, b int) int {\n"
        "    return a + b\n"
        "}\n",
        encoding="utf-8",
    )
    adapter = GoLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="go",
            generator_mode="heuristic",
        )
    )
    target = adapter.find_target("pkg/math.go::Add", adapter.discover(tmp_path))
    assert target is not None

    try:
        adapter.validate_model_output(
            "package generatedtests\n\n"
            "import (\n"
            '\t"testing"\n'
            ")\n\n"
            "func TestGenerated(t *testing.T) {\n"
            "\tgot := helper.Add(1, 1)\n"
            "\tif got != 2 {\n"
            '\t\tt.Fatalf("expected 2, got %v", got)\n'
            "\t}\n"
            "}\n",
            target,
        )
    except ValueError as exc:
        assert str(exc) == "Model output did not reference the requested target."
    else:
        raise AssertionError("Expected validator to reject references that do not come from the target package.")


def test_go_runner_reads_coverprofile(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    target_file = pkg_dir / "math.go"
    target_file.write_text(
        "package pkg\n\n"
        "func Add(a int, b int) int {\n"
        "    return a + b\n"
        "}\n",
        encoding="utf-8",
    )
    runner = GoRunner(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="go",
        )
    )
    coverage_path = tmp_path / "coverage.out"
    coverage_path.write_text(
        "mode: set\n"
        "pkg/math.go:3.2,3.18 1 1\n"
        "pkg/math.go:4.2,4.16 1 0\n",
        encoding="utf-8",
    )
    inventory = GoLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="go",
        )
    ).discover(tmp_path)
    target = next(function for function in inventory.functions if function.target_id == "pkg/math.go::Add")

    coverage = runner._read_coverage(
        coverage_path=coverage_path,
        copied_project_root=tmp_path,
        target=target,
    )

    assert coverage is not None
    assert coverage.statements == 2
    assert coverage.missing == 1
    assert coverage.coverage_percent == 50.0
    assert coverage.missing_lines == [4]


def test_go_runner_prefers_standard_windows_install_path(monkeypatch) -> None:
    monkeypatch.delenv("EVOTEST_GO_BIN", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(os, "name", "nt", raising=False)
    monkeypatch.setattr(
        Path,
        "exists",
        lambda self: str(self) == r"C:\Program Files\Go\bin\go.exe",
    )

    resolved = GoRunner._go_command()

    assert resolved == Path(r"C:\Program Files\Go\bin\go.exe")


def test_go_runner_sandbox_env_pins_go_runtime_dirs(tmp_path: Path) -> None:
    env = GoRunner._sandbox_env(tmp_path)

    assert env["GOCACHE"] == str(tmp_path / "gocache")
    assert env["GOPATH"] == str(tmp_path / "gopath")
    assert env["GOMODCACHE"] == str(tmp_path / "gopath" / "pkg" / "mod")
    assert env["GOTMPDIR"] == str(tmp_path)


def test_go_classifier_marks_missing_toolchain_as_nonrecoverable() -> None:
    classifier = GoFailureClassifier()

    failure = classifier.classify(
        ExecutionResult(
            target_id="pkg/math.go::Add",
            attempt_number=1,
            success=False,
            exit_code=127,
            stdout="",
            stderr="Go executable not found. Set EVOTEST_GO_BIN or install Go in a standard location.",
            duration_seconds=0.0,
            coverage=None,
            test_file_path=Path("generated.go"),
            language="go",
            adapter_id="go",
        )
    )

    assert failure is not None
    assert failure.category == "environment_integration"
    assert failure.subtype == "missing_dependency"
    assert failure.recoverable is False


def test_go_runner_treats_completed_timeout_as_success() -> None:
    assert GoRunner._timed_out_but_completed(
        stdout="ok  \texample.com/demo/evotest_generated_tests\t0.355s\tcoverage: 100.0% of statements\n",
        stderr="",
        coverage=type("Coverage", (), {"coverage_percent": 100.0})(),
    )
    assert not GoRunner._timed_out_but_completed(
        stdout="",
        stderr="",
        coverage=type("Coverage", (), {"coverage_percent": 100.0})(),
    )


def test_go_generator_infers_boolean_branch_string_returns() -> None:
    generator = GoHeuristicGenerator()

    expected_false = generator._infer_expected(
        (
            "func ChooseBucket(flag bool) string {\n"
            '    if flag { return "enabled" }\n'
            '    return "disabled"\n'
            "}\n"
        ),
        ["flag"],
        ["false"],
    )
    expected_true = generator._infer_expected(
        (
            "func ChooseBucket(flag bool) string {\n"
            '    if flag { return "enabled" }\n'
            '    return "disabled"\n'
            "}\n"
        ),
        ["flag"],
        ["true"],
    )

    assert expected_false == "disabled"
    assert expected_true == "enabled"


class _GoRepairSpyLLM:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate(
        self,
        *,
        target,
        prompt_text: str,
        output_dir: Path,
        attempt_number: int,
        generation_mode: str = "regenerate",
        parent_attempt_number: int | None = None,
        adapter=None,
    ) -> GeneratedTestArtifact:
        self.prompts.append(prompt_text)
        return GeneratedTestArtifact(
            target_id=target.target_id,
            attempt_number=attempt_number,
            prompt_text=prompt_text,
            test_file_path=output_dir / "repaired_test.go",
            test_source=self.response,
            generation_mode=generation_mode,
            parent_attempt_number=parent_attempt_number,
            provider_name="fake-openai",
            language="go",
            adapter_id="go",
        )


def test_go_repair_engine_builds_language_specific_llm_prompt(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "math.go").write_text(
        "package pkg\n\n"
        "func Add(a int, b int) int {\n"
        "    return a + b\n"
        "}\n",
        encoding="utf-8",
    )
    adapter = GoLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="go",
            generator_mode="llm",
        )
    )
    target = adapter.find_target("pkg/math.go::Add", adapter.discover(tmp_path))
    assert target is not None
    llm = _GoRepairSpyLLM(
        "package evotestgeneratedtests\n\n"
        "import (\n\t\"testing\"\n\ttargetpkg \"example.com/demo/pkg\"\n)\n\n"
        "func TestGenerated(t *testing.T) {\n\tif got := targetpkg.Add(1, 1); got != 2 {\n\t\tt.Fatalf(\"expected 2, got %v\", got)\n\t}\n}\n"
    )

    repaired = adapter.repair(
        target=target,
        previous_test_source="package evotestgeneratedtests\n\nfunc TestGenerated(t *testing.T) {}\n",
        failure=FailureClassification(
            category="assertion_semantics",
            subtype="assertion_failure",
            recoverable=True,
            message="expected 2, got 3",
            summary="wrong expectation",
        ),
        prompt_text='<evotest-metadata>{"prompt_version":"feedback-v3","strategy":"baseline"}</evotest-metadata>',
        attempt_number=2,
        output_dir=tmp_path,
        parent_attempt_number=1,
        llm_generator=llm,
    )

    assert repaired is not None
    assert repaired.provider_name == "fake-openai"
    assert "Language: go" in llm.prompts[0]
    assert "Current failing generated test:" in llm.prompts[0]
    assert "Return a full corrected Go _test.go file." in llm.prompts[0]


def test_go_repair_engine_does_not_count_plain_regeneration_as_repair(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "math.go").write_text(
        "package pkg\n\n"
        "func Add(a int, b int) int {\n"
        "    return a + b\n"
        "}\n",
        encoding="utf-8",
    )
    adapter = GoLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="go",
            generator_mode="heuristic",
        )
    )
    target = adapter.find_target("pkg/math.go::Add", adapter.discover(tmp_path))
    assert target is not None
    repair = GoRepairEngine()

    repaired = repair.repair(
        target=target,
        previous_test_source="package evotestgeneratedtests\n\nfunc TestGenerated(t *testing.T) {}\n",
        failure=FailureClassification(
            category="import_dependency",
            subtype="import_error",
            recoverable=True,
            message="bad import",
            summary="bad import",
        ),
        prompt_text='<evotest-metadata>{"prompt_version":"feedback-v3","strategy":"baseline"}</evotest-metadata>',
        attempt_number=2,
        output_dir=tmp_path,
        parent_attempt_number=1,
    )

    assert repaired is not None
    assert repaired.generation_mode == "regenerate"
