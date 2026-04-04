import json
from pathlib import Path

from evotest.adapters.go import GoLanguageAdapter
from evotest.adapters.javascript import JavaScriptLanguageAdapter
from evotest.benchmark import BenchmarkRunner
from evotest.config import HarnessConfig


def test_benchmark_runner_writes_metrics_report(tmp_path: Path) -> None:
    project_root = tmp_path / "fixture_project"
    package_root = project_root / "src" / "samplelib"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "math_add.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n",
        encoding="utf-8",
    )
    (package_root / "chooser.py").write_text(
        "def choose_bucket(flag):\n"
        "    if flag:\n"
        "        return 'enabled'\n"
        "    return 'disabled'\n",
        encoding="utf-8",
    )
    (package_root / "greeter.py").write_text(
        "class Greeter:\n"
        "    def __init__(self):\n"
        "        self.prefix = 'hi'\n\n"
        "    def greet(self, name):\n"
        "        return f'{self.prefix} {name}'\n",
        encoding="utf-8",
    )
    (package_root / "file_reader.py").write_text(
        "from pathlib import Path\n\n"
        "def read_text(file_path):\n"
        "    return Path(file_path).read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
    spec_path = tmp_path / "benchmarks.json"
    spec_path.write_text(
        json.dumps(
            {
                "targets": [
                    {"name": "add", "project_root": "fixture_project", "target": "src/samplelib/math_add.py::add"},
                    {
                        "name": "choose_bucket",
                        "project_root": "fixture_project",
                        "target": "src/samplelib/chooser.py::choose_bucket",
                    },
                    {
                        "name": "greet",
                        "project_root": "fixture_project",
                        "target": "src/samplelib/greeter.py::Greeter.greet",
                    },
                    {
                        "name": "read_text",
                        "project_root": "fixture_project",
                        "target": "src/samplelib/file_reader.py::read_text",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "report.json"
    runner = BenchmarkRunner(
        HarnessConfig(
            project_root=tmp_path,
            database_path=tmp_path / ".evotest" / "state.db",
            generator_mode="heuristic",
            max_iterations=3,
            max_runtime_seconds=15,
            coverage_target=80.0,
        )
    )

    report = runner.run(spec_path, report_path=report_path)

    assert report.summary.total_targets == 4
    assert report.summary.successful_targets == 4
    assert report.summary.pass_rate == 100.0
    assert report.summary.total_attempts >= 3
    assert report.summary.average_final_coverage >= 100.0
    assert report.summary.fallback_attempts == 0
    assert report.summary.invalid_output_attempts == 0
    assert report.summary.repair_attempts >= 1
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["pass_rate"] == 100.0
    choose_bucket = next(target for target in report.targets if target.name == "choose_bucket")
    assert choose_bucket.success is True
    assert all(target.language == "python" for target in report.targets)
    assert choose_bucket.repair_attempts == 1
    assert choose_bucket.repair_success_by_subtype["no_coverage_gain"]["successes"] == 1
    assert not (project_root / ".evotest").exists()


def test_benchmark_runner_loads_shipped_language_specific_specs(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    runner = BenchmarkRunner(
        HarnessConfig(
            project_root=repo_root,
            database_path=tmp_path / "state.db",
            language="auto",
        )
    )

    javascript_specs = runner.load_specs(repo_root / "benchmarks" / "javascript_targets.json")
    go_specs = runner.load_specs(repo_root / "benchmarks" / "go_targets.json")

    assert [spec.name for spec in javascript_specs] == ["add", "choose_bucket"]
    assert [spec.language for spec in javascript_specs] == ["javascript", "javascript"]
    assert all(spec.project_root.name == "javascript_project" for spec in javascript_specs)
    assert [spec.target_ref for spec in javascript_specs] == ["src/math.js::add", "src/chooser.js::chooseBucket"]

    assert [spec.name for spec in go_specs] == ["add", "choose_bucket"]
    assert [spec.language for spec in go_specs] == ["go", "go"]
    assert all(spec.project_root.name == "go_project" for spec in go_specs)
    assert [spec.target_ref for spec in go_specs] == ["pkg/math/add.go::Add", "pkg/chooser/chooser.go::ChooseBucket"]


def test_shipped_language_benchmark_targets_resolve_with_adapters(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    runner = BenchmarkRunner(
        HarnessConfig(
            project_root=repo_root,
            database_path=tmp_path / "state.db",
            language="auto",
        )
    )
    javascript_specs = runner.load_specs(repo_root / "benchmarks" / "javascript_targets.json")
    go_specs = runner.load_specs(repo_root / "benchmarks" / "go_targets.json")

    javascript_project_root = javascript_specs[0].project_root
    javascript_adapter = JavaScriptLanguageAdapter(
        HarnessConfig(
            project_root=javascript_project_root,
            database_path=tmp_path / "javascript.db",
            language="javascript",
        )
    )
    javascript_inventory = javascript_adapter.discover(javascript_project_root)
    for spec in javascript_specs:
        assert javascript_adapter.find_target(spec.target_ref, javascript_inventory) is not None

    go_project_root = go_specs[0].project_root
    go_adapter = GoLanguageAdapter(
        HarnessConfig(
            project_root=go_project_root,
            database_path=tmp_path / "go.db",
            language="go",
        )
    )
    go_inventory = go_adapter.discover(go_project_root)
    for spec in go_specs:
        assert go_adapter.find_target(spec.target_ref, go_inventory) is not None
