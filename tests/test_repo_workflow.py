from pathlib import Path

from autotestgen.config import HarnessConfig
from autotestgen.repo.models import BootstrapResult
from autotestgen.repo.detect import ProjectDetector
from autotestgen.repo.repair import SourceRepairOutcome
from autotestgen.repo.service import RepositoryWorkflowRunner


def test_project_detector_identifies_python_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\nversion = '0.1.0'\n",
        encoding="utf-8",
    )
    src_dir = tmp_path / "src" / "demo"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "maths.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    detection = ProjectDetector().detect(tmp_path)

    assert detection.language == "python"
    assert detection.framework == "pytest"
    assert detection.package_manager == "pip"
    assert detection.bootstrap_method == "pyproject.toml"


def test_repository_workflow_runs_local_repo_and_writes_outputs(tmp_path: Path) -> None:
    source_repo = tmp_path / "source_repo"
    src_dir = source_repo / "src" / "samplelib"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "maths.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (source_repo / "pyproject.toml").write_text(
        "[project]\nname = 'samplelib'\nversion = '0.1.0'\n",
        encoding="utf-8",
    )

    runner = RepositoryWorkflowRunner(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / "state.db",
            language="auto",
            generator_mode="heuristic",
            max_iterations=1,
            max_runtime_seconds=10,
            coverage_target=80.0,
            stability_reruns=1,
        )
    )

    report = runner.run(
        str(source_repo),
        max_targets=1,
        skip_bootstrap=True,
        workspace_root=tmp_path / "repo_runs",
    )

    assert report.clone_root != source_repo
    assert report.summary.total_targets == 1
    assert report.outputs.report_path.exists()
    assert report.outputs.review_path.exists()
    assert report.outputs.patch_path.exists()
    assert not (source_repo / ".autotestgen").exists()
    assert report.targets[0].generated_files
    generated_file = report.clone_root / report.targets[0].generated_files[0]
    assert generated_file.exists()
    patch_text = report.outputs.patch_path.read_text(encoding="utf-8")
    assert generated_file.name in patch_text
    review_text = report.outputs.review_path.read_text(encoding="utf-8")
    assert "AutoTestGen Review" in review_text
    assert report.targets[0].target_id in review_text


def test_project_detector_identifies_mixed_repo(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (tmp_path / "sample.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "math.js").write_text("export function add(a, b) { return a + b; }\n", encoding="utf-8")

    detection = ProjectDetector().detect(tmp_path, requested_language="auto")

    assert detection.language == "mixed"
    assert detection.languages == ["python", "javascript"]
    assert "pytest" in detection.framework
    assert "vitest" in detection.framework or "node:test" in detection.framework or "jest" in detection.framework


def test_repository_workflow_uses_auto_language_for_mixed_repo(tmp_path: Path) -> None:
    source_repo = tmp_path / "source_repo"
    source_repo.mkdir()
    (source_repo / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (source_repo / "sample.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (source_repo / "package.json").write_text('{"name":"demo"}', encoding="utf-8")
    src_dir = source_repo / "src"
    src_dir.mkdir()
    (src_dir / "math.js").write_text("export function add(a, b) { return a + b; }\n", encoding="utf-8")

    runner = RepositoryWorkflowRunner(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / "state.db",
            language="auto",
            generator_mode="heuristic",
        )
    )
    detection = ProjectDetector().detect(source_repo, requested_language="auto")

    repo_config = runner._repo_config(
        source_repo,
        detection,
        BootstrapResult(attempted=False, success=True),
    )

    assert detection.language == "mixed"
    assert repo_config.language == "auto"


class _FakeRepoRepairEngine:
    def __init__(self, replacement_source: str) -> None:
        self.replacement_source = replacement_source
        self.calls = 0

    def attempt(self, *, target, previous_attempt, adapter, repair_number):
        self.calls += 1
        target.file_path.write_text(self.replacement_source, encoding="utf-8")
        validation_result = adapter.run(
            target=target,
            test_source=previous_attempt.generated_test_source,
            test_file_path=previous_attempt.generated_test_path,
            attempt_number=10000 + repair_number,
        )
        return SourceRepairOutcome(
            attempted=True,
            applied=validation_result.success,
            verified=validation_result.success,
            source_file=target.file_path.relative_to(target.file_path.parents[1]).as_posix() if target.file_path.parents else target.file_path.name,
            provider_name="fake-repair",
            model_name="fake-model",
            error=None if validation_result.success else "validation failed",
            validation_result=validation_result,
        )


def test_repository_workflow_can_repair_repo_source_from_generated_test_feedback(tmp_path: Path) -> None:
    source_repo = tmp_path / "source_repo"
    source_repo.mkdir()
    (source_repo / "sample.py").write_text(
        "def add(a, b):\n    return missing_name + a + b\n",
        encoding="utf-8",
    )

    repair_engine = _FakeRepoRepairEngine(
        "def add(a, b):\n    return a + b\n"
    )
    runner = RepositoryWorkflowRunner(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / "state.db",
            language="python",
            generator_mode="heuristic",
            max_iterations=1,
            max_runtime_seconds=10,
            coverage_target=80.0,
            stability_reruns=1,
        ),
        repo_repair_engine=repair_engine,
    )

    report = runner.run(
        str(source_repo),
        max_targets=1,
        skip_bootstrap=True,
        workspace_root=tmp_path / "repo_runs",
        repair_repo=True,
        repo_repair_attempts=1,
    )

    assert repair_engine.calls == 1
    assert report.summary.successful_targets == 1
    assert report.summary.source_repair_attempts == 1
    assert report.summary.source_repair_successes == 1
    assert report.targets[0].source_repair_success is True
    assert any(path.endswith("sample.py") for path in report.targets[0].source_repair_files)
    patch_text = report.outputs.patch_path.read_text(encoding="utf-8")
    assert "sample.py" in patch_text
