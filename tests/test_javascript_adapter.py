import shutil
from pathlib import Path

from evotest.adapters.javascript import JavaScriptLanguageAdapter
from evotest.adapters.javascript.repair import JavaScriptRepairEngine
from evotest.adapters.javascript.runner import JavaScriptRunner
from evotest.benchmark import BenchmarkRunner
from evotest.config import HarnessConfig
from evotest.models import FailureClassification, GeneratedTestArtifact


def test_javascript_adapter_discovers_and_generates_heuristic_tests(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","devDependencies":{"vitest":"^1.0.0"}}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "math.js").write_text(
        "export function add(a, b) {\n"
        "  return a + b;\n"
        "}\n",
        encoding="utf-8",
    )
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".evotest" / "state.db",
        language="javascript",
        generator_mode="heuristic",
    )
    adapter = JavaScriptLanguageAdapter(config)

    inventory = adapter.discover(tmp_path)
    target = adapter.find_target("src/math.js::add", inventory)
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
        prompt_text=f'<evotest-metadata>{{"prompt_version":"feedback-v2","strategy":"baseline"}}</evotest-metadata>',
        output_dir=tmp_path,
        attempt_number=1,
    )

    assert context.language == "javascript"
    assert artifact.language == "javascript"
    assert "expect(result).toBe(2);" in artifact.test_source
    adapter.validate_model_output(
        "import { add } from './math.js';\n"
        "test('generated', () => {\n  expect(add(1, 1)).toBe(2);\n});\n",
        target,
    )


def test_javascript_validator_accepts_es_module_alias_imports(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","devDependencies":{"vitest":"^1.0.0"}}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "math.js").write_text(
        "export function add(a, b) {\n"
        "  return a + b;\n"
        "}\n",
        encoding="utf-8",
    )
    adapter = JavaScriptLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="javascript",
            generator_mode="heuristic",
        )
    )
    target = adapter.find_target("src/math.js::add", adapter.discover(tmp_path))
    assert target is not None

    adapter.validate_model_output(
        "import { add as subject } from './math.js';\n"
        "test('generated', () => {\n  expect(subject(1, 1)).toBe(2);\n});\n",
        target,
    )


def test_javascript_validator_rejects_bare_package_name_collision_import(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","devDependencies":{"vitest":"^1.0.0"}}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "math.js").write_text(
        "export function add(a, b) {\n"
        "  return a + b;\n"
        "}\n",
        encoding="utf-8",
    )
    adapter = JavaScriptLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="javascript",
            generator_mode="heuristic",
        )
    )
    target = adapter.find_target("src/math.js::add", adapter.discover(tmp_path))
    assert target is not None

    try:
        adapter.validate_model_output(
            "import { add } from 'math';\n"
            "test('generated', () => {\n  expect(add(1, 1)).toBe(2);\n});\n",
            target,
        )
    except ValueError as exc:
        assert str(exc) == "Model output did not reference the requested target."
    else:
        raise AssertionError("Expected bare package imports to be rejected for file-backed targets.")


def test_javascript_validator_rejects_namespace_import_from_bare_package_subpath(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","type":"module"}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "math.js").write_text(
        "export function add(a, b) {\n"
        "  return a + b;\n"
        "}\n",
        encoding="utf-8",
    )
    adapter = JavaScriptLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="javascript",
            generator_mode="heuristic",
        )
    )
    target = adapter.find_target("src/math.js::add", adapter.discover(tmp_path))
    assert target is not None

    try:
        adapter.validate_model_output(
            "import * as subject from 'vendor/math';\n"
            "test('generated', () => {\n  expect(subject.add(1, 1)).toBe(2);\n});\n",
            target,
        )
    except ValueError as exc:
        assert str(exc) == "Model output did not reference the requested target."
    else:
        raise AssertionError("Expected package subpath imports to be rejected for file-backed targets.")


def test_javascript_validator_rejects_invalid_syntax(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","devDependencies":{"vitest":"^1.0.0"}}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "math.js").write_text(
        "export function add(a, b) {\n"
        "  return a + b;\n"
        "}\n",
        encoding="utf-8",
    )
    adapter = JavaScriptLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="javascript",
            generator_mode="heuristic",
        )
    )
    target = adapter.find_target("src/math.js::add", adapter.discover(tmp_path))
    assert target is not None

    try:
        adapter.validate_model_output(
            "import { add } from './math.js';\n\n"
            "test('generated', () => {\n"
            "  expect(add(1, 1)).toBe(2);\n",
            target,
        )
    except ValueError as exc:
        assert str(exc) == "Model output did not parse as JavaScript."
    else:
        raise AssertionError("Expected invalid JavaScript syntax to be rejected.")


def test_javascript_validator_rejects_wrong_object_method_calls(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","devDependencies":{"vitest":"^1.0.0"}}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "math.js").write_text(
        "export function add(a, b) {\n"
        "  return a + b;\n"
        "}\n",
        encoding="utf-8",
    )
    adapter = JavaScriptLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="javascript",
            generator_mode="heuristic",
        )
    )
    target = adapter.find_target("src/math.js::add", adapter.discover(tmp_path))
    assert target is not None

    try:
        adapter.validate_model_output(
            "test('generated', () => {\n"
            "  const calculator = { add(a, b) { return a + b; } };\n"
            "  expect(calculator.add(1, 1)).toBe(2);\n"
            "});\n",
            target,
        )
    except ValueError as exc:
        assert str(exc) == "Model output did not reference the requested target."
    else:
        raise AssertionError("Expected target reference validation to reject unrelated object method calls.")


def test_javascript_runner_copy_ignores_node_modules(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","devDependencies":{"vitest":"^1.0.0"}}', encoding="utf-8")
    node_pkg_dir = tmp_path / "node_modules" / "pkg"
    node_pkg_dir.mkdir(parents=True)
    (node_pkg_dir / "index.js").write_text("export const value = 1;\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "math.js").write_text("export function add(a, b) { return a + b; }\n", encoding="utf-8")
    runner = JavaScriptRunner(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="javascript",
        )
    )
    destination = tmp_path / "workspace"

    runner._copy_project(destination)

    assert not (destination / "node_modules" / "pkg" / "index.js").exists()


def test_javascript_adapter_discovers_default_exported_function(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","type":"module"}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "default_math.js").write_text(
        "export default function add(a, b) {\n"
        "  return a + b;\n"
        "}\n",
        encoding="utf-8",
    )
    adapter = JavaScriptLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="javascript",
        )
    )

    inventory = adapter.discover(tmp_path)

    assert any(function.target_id == "src/default_math.js::add" for function in inventory.functions)


def test_javascript_adapter_discovers_commonjs_module_exports(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "commonjs_math.js").write_text(
        "function add(a, b) {\n"
        "  return a + b;\n"
        "}\n\n"
        "module.exports = { add };\n",
        encoding="utf-8",
    )
    adapter = JavaScriptLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="javascript",
        )
    )

    inventory = adapter.discover(tmp_path)

    assert any(function.target_id == "src/commonjs_math.js::add" for function in inventory.functions)


def test_javascript_adapter_discovers_commonjs_default_export_assignment(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "commonjs_default.js").write_text(
        "function add(a, b) {\n"
        "  return a + b;\n"
        "}\n\n"
        "module.exports = add;\n",
        encoding="utf-8",
    )
    adapter = JavaScriptLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="javascript",
        )
    )

    inventory = adapter.discover(tmp_path)

    assert any(function.target_id == "src/commonjs_default.js::add" for function in inventory.functions)


def test_javascript_generator_loads_commonjs_default_export_function(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "commonjs_default.js").write_text(
        "function add(a, b) {\n"
        "  return a + b;\n"
        "}\n\n"
        "module.exports = add;\n",
        encoding="utf-8",
    )
    adapter = JavaScriptLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="javascript",
            generator_mode="heuristic",
        )
    )

    inventory = adapter.discover(tmp_path)
    target = adapter.find_target("src/commonjs_default.js::add", inventory)
    assert target is not None

    artifact = adapter.generate_heuristic(
        target=target,
        prompt_text='<evotest-metadata>{"prompt_version":"feedback-v2","strategy":"baseline"}</evotest-metadata>',
        output_dir=tmp_path,
        attempt_number=1,
    )

    assert "__evotestResolveExport" in artifact.test_source
    assert "loadedModule.default" in artifact.test_source
    assert "add = __evotestResolveExport(loadedModule, 'add');" in artifact.test_source


def test_javascript_benchmark_runs_without_installed_framework(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        return
    repo_root = Path(__file__).resolve().parents[1]
    report_path = tmp_path / "javascript_benchmark_report.json"
    runner = BenchmarkRunner(
        HarnessConfig(
            project_root=repo_root,
            database_path=tmp_path / "state.db",
            artifacts_root=tmp_path / ".evotest",
            language="javascript",
            generator_mode="heuristic",
            max_iterations=3,
            max_runtime_seconds=15,
            coverage_target=80.0,
            stability_reruns=1,
        )
    )

    report = runner.run(repo_root / "benchmarks" / "javascript_targets.json", report_path=report_path)

    assert report.summary.total_targets == 2
    assert report.summary.successful_targets == 2
    assert report.summary.pass_rate == 100.0
    assert all(target.language == "javascript" for target in report.targets)
    assert report_path.exists()


class _RepairSpyLLM:
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
            test_file_path=output_dir / "repaired.test.js",
            test_source=self.response,
            generation_mode=generation_mode,
            parent_attempt_number=parent_attempt_number,
            provider_name="fake-openai",
            language="javascript",
            adapter_id="javascript",
        )


def test_javascript_repair_engine_builds_language_specific_llm_prompt(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","devDependencies":{"vitest":"^1.0.0"}}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "math.js").write_text("export function add(a, b) { return a + b; }\n", encoding="utf-8")
    adapter = JavaScriptLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="javascript",
            generator_mode="llm",
        )
    )
    target = adapter.find_target("src/math.js::add", adapter.discover(tmp_path))
    assert target is not None
    llm = _RepairSpyLLM(
        "import { add } from './math.js';\n"
        "test('generated', () => {\n"
        "  expect(add(1, 1)).toBe(2);\n"
        "});\n"
    )

    repaired = adapter.repair(
        target=target,
        previous_test_source="test('generated', () => { expect(add(1, 1)).toBe(3); });\n",
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
    assert "Language: javascript" in llm.prompts[0]
    assert "Current failing generated test:" in llm.prompts[0]
    assert "Return a full corrected JavaScript/TypeScript vitest/jest test file." in llm.prompts[0]


def test_javascript_repair_engine_does_not_count_plain_regeneration_as_repair(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","devDependencies":{"vitest":"^1.0.0"}}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "math.js").write_text("export function add(a, b) { return a + b; }\n", encoding="utf-8")
    repair = JavaScriptRepairEngine(tmp_path)
    adapter = JavaScriptLanguageAdapter(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            language="javascript",
            generator_mode="heuristic",
        )
    )
    target = adapter.find_target("src/math.js::add", adapter.discover(tmp_path))
    assert target is not None

    repaired = repair.repair(
        target=target,
        previous_test_source="test('generated', () => { expect(add(1, 1)).toBe(2); });\n",
        failure=FailureClassification(
            category="import_dependency",
            subtype="import_error",
            recoverable=True,
            message="cannot import module",
            summary="bad import",
        ),
        prompt_text='<evotest-metadata>{"prompt_version":"feedback-v3","strategy":"baseline"}</evotest-metadata>',
        attempt_number=2,
        output_dir=tmp_path,
        parent_attempt_number=1,
    )

    assert repaired is not None
    assert repaired.generation_mode == "regenerate"
