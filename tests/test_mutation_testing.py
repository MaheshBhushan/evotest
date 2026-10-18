import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from evotest.benchmark.runner import BenchmarkRunner, BenchmarkTargetSpec
from evotest.cli import build_config, build_parser, main
from evotest.config import HarnessConfig
from evotest.models import ExecutionResult, ModuleTarget, MutationResult, OrchestrationResult
from evotest.orchestrator import ClosedLoopOrchestrator
from evotest.repo.models import (
    BootstrapResult,
    ProjectDetection,
    RepositoryOutputPaths,
    RepositoryRunReport,
)
from evotest.repo.service import RepositoryWorkflowRunner
from evotest.runner.mutmut_runner import MutmutRunner
from evotest.runner.pytest_runner import PytestRunner
from evotest.storage import SQLiteStore


def make_config(root: Path) -> HarnessConfig:
    return HarnessConfig(
        project_root=root, database_path=root / ".evotest" / "state.db",
        generator_mode="heuristic", stability_reruns=1,
        mutation_testing=True, mutation_timeout_seconds=15,
    )


@pytest.mark.skipif(not hasattr(os, "fork"), reason="mutmut 3 requires fork support")
def test_real_mutmut_distinguishes_weak_oracles_and_preserves_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    # Both suites execute every source line. Only the strong oracle checks the result.
    original = "def add(a, b):\n    return a + b\n"
    source = tmp_path / "sample.py"
    source.write_text(original, encoding="utf-8")
    config = make_config(tmp_path)
    target = ModuleTarget("sample.py", source, "sample", original)
    weak = PytestRunner(config).run(
        target, "from sample import add\ndef test_add():\n    add(2, 3)\n",
        config.test_output_dir / "test_weak.py", 1,
    )
    assert weak.success and weak.coverage.coverage_percent == 100.0
    assert weak.mutation.status == "completed", weak.mutation.error
    assert weak.mutation.counts.get("survived", 0) > 0
    evidence = Path(weak.mutation.evidence_dir)
    counterexamples = json.loads((evidence / "counterexamples.json").read_text())
    assert any(item["status"] == "survived" and "@@" in item["diff"] for item in counterexamples)
    assert (evidence / "generated_test.py").read_text().endswith("    add(2, 3)\n")
    assert (evidence / "source" / "sample.py").read_text() == original
    assert (evidence / "mutants" / "sample.py.meta").exists()
    assert source.read_text() == original

    orchestrator = ClosedLoopOrchestrator(config)
    strong = orchestrator.run_target("sample.py::add")
    assert strong.final_success and strong.stability_passed
    assert strong.mutation.status == "completed", strong.mutation.error
    assert strong.mutation.score > weak.mutation.score
    assert len(list((config.artifacts_dir / "mutations").iterdir())) == 2  # stability rerun adds none
    with orchestrator.store.connect() as connection:
        row = connection.execute("SELECT mutation_payload_json FROM results").fetchone()
    assert json.loads(row[0]) == strong.mutation.to_dict()

    benchmark = BenchmarkRunner(config)
    target_report = benchmark._target_report(BenchmarkTargetSpec("add", tmp_path, "sample.py::add"), strong)
    assert target_report.to_dict()["mutation"]["score"] == strong.mutation.score
    assert benchmark._build_summary([target_report]).average_mutation_score == strong.mutation.score

    repo = RepositoryWorkflowRunner(config)
    repo_target = repo._target_report(config, strong, [])
    summary = repo._build_summary([repo_target])
    assert repo_target.to_dict()["mutation"]["evidence_dir"] == strong.mutation.evidence_dir
    assert summary.average_mutation_score == strong.mutation.score
    report = RepositoryRunReport(
        "fixture", tmp_path, tmp_path, ProjectDetection("python", "pytest", "pip", "none"),
        BootstrapResult(False, True), RepositoryOutputPaths(tmp_path / "report", tmp_path / "review", tmp_path / "patch"),
        summary, [repo_target],
    )
    assert f"Mutation score: `{strong.mutation.score:.2f}%`" in repo._build_review(report)
    monkeypatch.setattr(ClosedLoopOrchestrator, "run_target", lambda self, target: strong)
    monkeypatch.setattr(sys, "argv", ["evotest", "--project", str(tmp_path), "run", "--target", "sample.py::add"])
    main()
    assert f"Mutation Score: {strong.mutation.score:.2f}%" in capsys.readouterr().out


@pytest.mark.parametrize("outcome", ["timeout", "error", "partial", "empty", "unexpected"])
def test_unavailable_scores_are_not_fabricated(tmp_path: Path, monkeypatch, outcome: str) -> None:
    config = make_config(tmp_path)
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    test = tmp_path / "test_generated.py"
    test.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    runner = MutmutRunner(config, sys.executable)

    def command(args, project, env, deadline, evidence):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(args, 1)
        if outcome == "error":
            raise RuntimeError("mutmut failed")
        if args[0] == "results":
            return {"partial": "  sample.x: not checked\n", "empty": "", "unexpected": "bad output\n"}[outcome]
        return ""

    monkeypatch.setattr(runner, "_command", command)
    result = runner.run(tmp_path, ModuleTarget("sample.py", source, "sample", source.read_text()), test, {})
    assert result.score is None
    assert result.status == {"timeout": "timeout", "empty": "completed"}.get(outcome, "error")
    assert "N/A" in result.display()
    assert json.loads((Path(result.evidence_dir) / "result.json").read_text())["score"] is None


def test_flags_propagate_to_project_configs_and_unsupported_languages_stay_unscored(tmp_path: Path) -> None:
    args = build_parser().parse_args([
        "--project", str(tmp_path), "--mutation-testing", "--mutation-timeout", "12", "discover",
    ])
    config = build_config(args).resolved()
    assert config.mutation_testing and config.mutation_timeout_seconds == 12
    benchmark_config = BenchmarkRunner(config)._config_for_project(tmp_path, "demo", tmp_path, 1, "python")
    repo_config = RepositoryWorkflowRunner(config)._repo_config(
        tmp_path, ProjectDetection("python", "pytest", "pip", "none"), BootstrapResult(False, True),
    )
    assert benchmark_config.mutation_testing and benchmark_config.mutation_timeout_seconds == 12
    assert repo_config.mutation_testing and repo_config.mutation_timeout_seconds == 12
    result = OrchestrationResult("sample.js", [], True, 100.0, True, "javascript", "javascript")
    assert result.mutation is None


def test_sqlite_migrates_existing_results_and_clears_previous_mutation(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "state.db")
    store.initialize()
    with store.connect() as connection:
        connection.execute("ALTER TABLE results DROP COLUMN mutation_payload_json")
    store.initialize()
    result = ExecutionResult("sample.py", 1, True, 0, "", "", 0.1, None, tmp_path / "test.py")
    result.mutation = MutationResult("completed", score=0.0, counts={"survived": 2})
    store.save_execution_result(result)
    with store.connect() as connection:
        assert json.loads(connection.execute("SELECT mutation_payload_json FROM results").fetchone()[0])["score"] == 0.0
    result.mutation = None
    store.save_execution_result(result)
    with store.connect() as connection:
        assert connection.execute("SELECT mutation_payload_json FROM results").fetchone()[0] is None


def test_failed_generated_suite_does_not_run_mutmut(tmp_path: Path, monkeypatch) -> None:
    config = make_config(tmp_path)
    source = tmp_path / "sample.py"
    source.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(MutmutRunner, "run", lambda *args: pytest.fail("must not mutate a failing suite"))
    result = PytestRunner(config).run(
        ModuleTarget("sample.py", source, "sample", source.read_text()),
        "def test_bad():\n    assert False\n", config.test_output_dir / "test_bad.py", 1,
    )
    assert not result.success and result.mutation is None


@pytest.mark.skipif(not hasattr(os, "fork"), reason="mutmut 3 requires fork support")
def test_mutation_uses_src_package_and_generated_suite_despite_project_config(tmp_path: Path) -> None:
    package = tmp_path / "src" / "samplelib"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    source = package / "maths.py"
    original = "from samplelib.constants import OFFSET\ndef add(a, b):\n    return a + b + OFFSET\n"
    source.write_text(original, encoding="utf-8")
    (package / "constants.py").write_text("OFFSET = 0\n", encoding="utf-8")
    project_config = '[tool.mutmut]\nsource_paths = ["missing"]\n[tool.pytest.ini_options]\naddopts = "-q"\n'
    (tmp_path / "pyproject.toml").write_text(project_config, encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_existing.py").write_text("def test_existing():\n    assert False\n", encoding="utf-8")
    config = make_config(tmp_path)
    result = PytestRunner(config).run(
        ModuleTarget("src/samplelib/maths.py", source, "samplelib.maths", original),
        "from samplelib.maths import add\ndef test_add():\n    assert add(2, 3) == 5\n",
        config.test_output_dir / "test_maths.py", 1,
    )
    assert result.success and result.mutation.status == "completed", result.mutation.error
    assert result.mutation.counts.get("killed", 0) > 0
    counterexamples = json.loads((Path(result.mutation.evidence_dir) / "counterexamples.json").read_text())
    assert all(item["id"].startswith("samplelib.maths.") for item in counterexamples)
    assert source.read_text() == original
    assert (tmp_path / "pyproject.toml").read_text() == project_config
