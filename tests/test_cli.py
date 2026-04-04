from pathlib import Path

from evotest.cli import build_config, build_parser


def test_build_config_reads_generation_settings(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    parser = build_parser()
    args = parser.parse_args(
        [
            "--project",
            str(tmp_path),
            "--generator-mode",
            "llm",
            "--model",
            "gpt-5.4-mini",
            "--temperature",
            "0.4",
            "--max-output-tokens",
            "2048",
            "--stability-reruns",
            "2",
            "discover",
        ]
    )

    config = build_config(args)

    assert config.project_root == tmp_path.resolve()
    assert config.language == "auto"
    assert config.generator_mode == "llm"
    assert config.model_name == "gpt-5.4-mini"
    assert config.temperature == 0.4
    assert config.max_output_tokens == 2048
    assert config.stability_reruns == 2
    assert config.openai_api_key == "test-key"


def test_build_parser_supports_benchmark_command(tmp_path: Path) -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "--project",
            str(tmp_path),
            "benchmark",
            "--spec",
            "benchmarks/custom.json",
            "--report",
            ".evotest/custom-report.json",
        ]
    )

    assert args.command == "benchmark"
    assert args.spec == "benchmarks/custom.json"
    assert args.report == ".evotest/custom-report.json"


def test_build_parser_defaults_language_to_auto() -> None:
    parser = build_parser()

    args = parser.parse_args(["discover"])

    assert args.language == "auto"


def test_build_parser_supports_repo_run_subject_and_output() -> None:
    parser = build_parser()

    args = parser.parse_args(
        [
            "run",
            "https://github.com/org/repo",
            "--repair-repo",
            "--repo-repair-attempts",
            "2",
            "--output",
            "patch",
            "--max-targets",
            "2",
        ]
    )

    assert args.command == "run"
    assert args.subject == "https://github.com/org/repo"
    assert args.repair_repo is True
    assert args.repo_repair_attempts == 2
    assert args.output == "patch"
    assert args.max_targets == 2
