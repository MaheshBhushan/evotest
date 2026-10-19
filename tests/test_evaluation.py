import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from evotest import evaluation
from evotest.config import HarnessConfig
from evotest.evaluation import EvaluationRunner
from evotest.feedback.representation import apply_feedback, attempt_feedback
from evotest.generator.provider import OpenAIResponsesProvider, ProviderResponseError
from evotest.models import (
    AttemptRecord,
    CoverageResult,
    ExecutionResult,
    FailureClassification,
    OrchestrationResult,
)
from evotest.orchestrator import ClosedLoopOrchestrator


def config_for(project: Path) -> HarnessConfig:
    return HarnessConfig(
        project_root=project, database_path=project / ".evotest/state.db",
        generator_mode="heuristic", max_iterations=2, stability_reruns=0,
        python_executable=sys.executable,
    )


def test_compare_measures_real_loop_repairs_and_changes_feedback(tmp_path, monkeypatch):
    (tmp_path / "sample.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "tasks.json").write_text(json.dumps({"targets": [{"name": "addition", "target": "sample.py::add"}]}))
    prompts = []

    def orchestrator_factory(config):
        orchestrator = ClosedLoopOrchestrator(config)
        provider = OpenAIResponsesProvider(api_key="offline-test")
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            prompts.append((config.feedback_format, kwargs["input"]))
            expected = 4 if len(calls) == 1 else 3
            return SimpleNamespace(
                output_text=f"from sample import add\ndef test_add():\n    assert add(1, 2) == {expected}\n",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )

        provider._client = SimpleNamespace(responses=SimpleNamespace(create=create))
        orchestrator.generator.provider = provider
        return orchestrator

    monkeypatch.setattr(evaluation, "ClosedLoopOrchestrator", orchestrator_factory)
    config = replace(config_for(tmp_path), generator_mode="llm", openai_api_key="offline-test")
    report = EvaluationRunner(config).compare("tasks.json")
    assert len(report["runs"]) == 2
    assert len({run["report_path"] for run in report["runs"]}) == 2
    for run in report["runs"]:
        assert run["success"] is True
        assert run["repair_attempts"] == run["repair_successes"] == 1
        assert run["repair_success_rate"] == 1
        assert run["token_usage"] == {"input_tokens": 200, "output_tokens": 40, "total_tokens": 240}
        assert run["latency_seconds"] > 0
        # The existing runner may lack coverage after pytest stops on a failure.
        assert run["final_coverage"] == 100
        initial = run["initial_coverage"]
        expected_delta = 100 - initial if initial is not None else None
        assert run["coverage_delta"] == expected_delta
        assert run["attempts"][1]["coverage_delta"] == expected_delta
        saved = json.loads(Path(run["report_path"]).read_text())
        assert saved == {key: value for key, value in run.items() if key != "task"}
    assert prompts[0][1] == prompts[2][1]  # Initial tasks are identical.
    compact_prompt, raw_prompt = prompts[1][1], prompts[3][1]
    assert '"stdout"' not in compact_prompt
    assert '"stdout"' in raw_prompt
    assert "assert 3 == 4" not in compact_prompt
    assert len(compact_prompt) < len(raw_prompt)
    assert report["summary"]["compact"]["token_usage"]["total_tokens"] == 240
    assert report["summary"]["raw"]["repair_success_rate"] == 1
    assert Path(report["report_path"]).exists()


def test_feedback_bounds_summary_and_preserves_raw_evidence():
    failure = FailureClassification("runtime", "value_error", True, "full traceback", "s" * 500)
    attempt = AttemptRecord("sample.py::add", 1, "", Path("test_sample.py"), "", failure=failure)
    attempt.execution_result = ExecutionResult(
        attempt.target_id, 1, False, 1, "raw stdout", "raw stderr", 0.1,
        CoverageResult(100, 100, 0, list(range(100))), attempt.generated_test_path,
    )
    compact = attempt_feedback(attempt, "compact")
    raw = attempt_feedback(attempt, "raw")
    assert len(compact["failure"]["summary"]) == 240
    assert len(compact["missing_lines"]) == 50
    assert compact["missing_lines_truncated"] is True
    assert "message" not in compact["failure"]
    assert raw["stderr"] == "raw stderr"
    assert raw["failure"]["message"] == "full traceback"
    context = SimpleNamespace(previous_failures=["old"], execution_evidence=["old"])
    apply_feedback(context, attempt, "compact")
    assert "full traceback" not in context.execution_evidence[0]
    assert context.previous_failures == ["value_error: " + "s" * 240]


def test_missing_metrics_and_negative_coverage_are_not_fabricated(tmp_path, monkeypatch):
    (tmp_path / "sample.py").write_text("def add(a, b):\n    return a + b\n")
    first = AttemptRecord("sample.py::add", 1, "", Path("test_sample.py"), "")
    second = replace(first, attempt_number=2)
    for attempt, percent in ((first, 80), (second, 40)):
        attempt.execution_result = ExecutionResult(
            attempt.target_id, attempt.attempt_number, False, 1, "", "", 0.1,
            CoverageResult(10, 2, percent), attempt.generated_test_path,
        )
    result = OrchestrationResult(first.target_id, [first, second], False, 80, False)

    def factory(config):
        config.artifacts_dir.mkdir(parents=True)
        return SimpleNamespace(run_target=lambda _: result, generator=SimpleNamespace(provider=SimpleNamespace(usage_records=[None])))

    monkeypatch.setattr(evaluation, "ClosedLoopOrchestrator", factory)
    runner = EvaluationRunner(config_for(tmp_path))
    report = runner.evaluate(first.target_id)
    assert report["attempts"][1]["coverage_delta"] == -40
    assert report["token_usage"] is None
    assert report["repair_success_rate"] is None
    first.execution_result = second.execution_result = None
    report = runner.evaluate(first.target_id)
    assert report["initial_coverage"] is report["final_coverage"] is report["coverage_delta"] is None


def test_provider_records_usage_even_for_invalid_output_and_marks_failures_unknown():
    provider = OpenAIResponsesProvider(api_key="offline-test")
    responses = iter([
        SimpleNamespace(output_text="", usage=SimpleNamespace(input_tokens=12, output_tokens=3)),
        SimpleNamespace(output_text="code", usage=None),
    ])
    provider._client = SimpleNamespace(responses=SimpleNamespace(create=lambda **_: next(responses)))
    kwargs = {"model": "test-model", "temperature": 0, "max_output_tokens": 100}
    with pytest.raises(ProviderResponseError, match="empty output"):
        provider.generate_text("prompt", **kwargs)
    provider.generate_text("prompt", **kwargs)
    assert provider.usage_records == [{"input_tokens": 12, "output_tokens": 3}, None]
    with pytest.raises(ProviderResponseError, match="request failed"):
        provider.generate_text("prompt", **kwargs)
    assert provider.usage_records[-1] is None


def test_evaluation_rejects_invalid_formats_paths_and_missing_llm_key(tmp_path):
    runner = EvaluationRunner(config_for(tmp_path))
    for target, representation in (("../sample.py", "compact"), ("sample.py", "invalid")):
        with pytest.raises(ValueError):
            runner.evaluate(target, representation)
    outside = tmp_path.parent / "outside"
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="within"):
        runner.evaluate("escape/sample.py")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        EvaluationRunner(replace(runner.config, generator_mode="llm")).evaluate("sample.py")
    with pytest.raises(ValueError, match="iteration"):
        EvaluationRunner(replace(runner.config, max_iterations=0)).evaluate("sample.py")
    (tmp_path / "empty.json").write_text('{"targets": []}')
    with pytest.raises(ValueError, match="1 to 20"):
        runner.compare("empty.json")
    (tmp_path / "escape.json").write_text('{"targets": [{"project_root": "..", "target": "sample.py"}]}')
    with pytest.raises(ValueError, match="within"):
        runner.compare("escape.json")
    assert not runner.config.artifacts_dir.exists()


def test_mcp_stdio_handshake_tools_evaluation_comparison_and_errors(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    (tmp_path / "sample.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "tasks.json").write_text('[{"target": "sample.py::add"}]')

    async def check():
        params = StdioServerParameters(command=sys.executable, args=[
            "-m", "evotest.cli", "--project", str(tmp_path), "--language", "python",
            "--generator-mode", "heuristic", "--stability-reruns", "0", "mcp",
        ])
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert set(tools) == {"evaluate_target", "compare_feedback"}
            assert tools["evaluate_target"].inputSchema["properties"]["feedback_format"]["enum"] == ["compact", "raw"]
            evaluated = await session.call_tool("evaluate_target", {"target": "sample.py::add"})
            assert not evaluated.isError
            report = evaluated.structuredContent
            assert report["success"] is True
            assert report["token_usage"]["total_tokens"] == 0
            assert report["attempts"][0]["coverage_percent"] == 100
            compared = await session.call_tool("compare_feedback", {"spec": "tasks.json"})
            assert not compared.isError
            assert len(compared.structuredContent["runs"]) == 2
            for args in ({"target": "missing.py"}, {"target": "../outside.py"}, {"target": "sample.py", "feedback_format": "wrong"}):
                failed = await session.call_tool("evaluate_target", args)
                assert failed.isError

    asyncio.run(asyncio.wait_for(check(), timeout=15))
