from __future__ import annotations

import argparse
import os
from pathlib import Path

from autotestgen.benchmark import BenchmarkRunner
from autotestgen.config import HarnessConfig
from autotestgen.orchestrator import ClosedLoopOrchestrator
from autotestgen.repo import RepositoryWorkflowRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autotestgen")
    parser.add_argument("--project", default=".", help="Project root to analyze.")
    parser.add_argument("--db", default=".autotestgen/state.db", help="SQLite database path.")
    parser.add_argument(
        "--language",
        choices=["python", "javascript", "go", "auto"],
        default="auto",
        help="Language adapter to use. auto detects from project files.",
    )
    parser.add_argument("--max-iterations", type=int, default=5, help="Max retry attempts per target.")
    parser.add_argument("--max-runtime", type=int, default=10, help="Per-attempt pytest timeout.")
    parser.add_argument("--coverage-target", type=float, default=80.0, help="Coverage target percent.")
    parser.add_argument("--stability-reruns", type=int, default=3, help="How many reruns to use for stability checks.")
    parser.add_argument(
        "--generator-mode",
        choices=["auto", "llm", "heuristic"],
        default="auto",
        help="Choose the generator backend.",
    )
    parser.add_argument("--model", default="gpt-5.4", help="Model to use for LLM-backed generation.")
    parser.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature for model generation.")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=4000,
        help="Maximum output tokens for model-backed generation.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("discover", help="Discover test targets for the selected language.")

    run_parser = subparsers.add_parser("run", help="Run the closed-loop harness for one target.")
    run_parser.add_argument("subject", nargs="?", help="GitHub repo URL or local project path.")
    run_parser.add_argument("--target", help="Target ref like path.py::function or path.py.")
    run_parser.add_argument("--branch", help="Git branch to clone for repo workflows.")
    run_parser.add_argument("--commit", help="Exact commit to checkout for repo workflows.")
    run_parser.add_argument("--ref", help="Git ref to fetch and checkout for repo workflows.")
    run_parser.add_argument("--max-targets", type=int, default=3, help="Max discovered targets to run for repo workflows.")
    run_parser.add_argument(
        "--workspace-root",
        help="Where repo workflow workspaces and outputs should be created. Defaults under --project/.autotestgen_repo_runs.",
    )
    run_parser.add_argument("--skip-bootstrap", action="store_true", help="Skip dependency bootstrap in repo workflows.")
    run_parser.add_argument(
        "--repair-repo",
        action="store_true",
        help="Attempt source-code repairs in repo workflows using generated test feedback.",
    )
    run_parser.add_argument(
        "--repo-repair-attempts",
        type=int,
        default=1,
        help="Maximum source repair attempts per target in repo workflows.",
    )
    run_parser.add_argument(
        "--output",
        choices=["all", "report", "summary", "patch"],
        default="all",
        help="What to print for repo workflows.",
    )

    benchmark_parser = subparsers.add_parser("benchmark", help="Run a benchmark suite and write a metrics report.")
    benchmark_parser.add_argument(
        "--spec",
        default="benchmarks/default_targets.json",
        help="JSON benchmark spec file. Relative paths resolve from --project.",
    )
    benchmark_parser.add_argument(
        "--report",
        default=".autotestgen/benchmark_report.json",
        help="Where to write the benchmark metrics report.",
    )
    return parser


def build_config(args: argparse.Namespace) -> HarnessConfig:
    project_root = Path(args.project).resolve()
    database_path = (project_root / args.db).resolve() if not Path(args.db).is_absolute() else Path(args.db)
    return HarnessConfig(
        project_root=project_root,
        database_path=database_path,
        language=args.language,
        max_iterations=args.max_iterations,
        max_runtime_seconds=args.max_runtime,
        coverage_target=args.coverage_target,
        stability_reruns=args.stability_reruns,
        generator_mode=args.generator_mode,
        model_name=args.model,
        temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        openai_api_key=os.getenv("OPENAI_API_KEY"),
    )


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    config = build_config(args)

    if args.command == "discover":
        orchestrator = ClosedLoopOrchestrator(config)
        inventory = orchestrator.discover()
        print("Functions:")
        for function in inventory.functions:
            print(f"  {function.target_id}")
        print("Modules:")
        for module in inventory.modules:
            print(f"  {module.target_id}")
        return

    if args.command == "run":
        if args.subject and (
            args.subject.startswith("http://")
            or args.subject.startswith("https://")
            or args.subject.startswith("git@")
            or args.subject.endswith(".git")
            or Path(args.subject).exists()
        ):
            workflow_runner = RepositoryWorkflowRunner(config)
            report = workflow_runner.run(
                args.subject,
                branch=args.branch,
                commit=args.commit,
                ref=args.ref,
                target_ref=args.target,
                max_targets=args.max_targets,
                workspace_root=Path(args.workspace_root) if args.workspace_root else None,
                skip_bootstrap=args.skip_bootstrap,
                repair_repo=args.repair_repo,
                repo_repair_attempts=args.repo_repair_attempts,
            )
            if args.output == "report":
                print(report.outputs.report_path.read_text(encoding="utf-8"))
                return
            if args.output == "summary":
                print(report.outputs.review_path.read_text(encoding="utf-8"))
                return
            if args.output == "patch":
                print(report.outputs.patch_path.read_text(encoding="utf-8"))
                return
            print(f"Source: {report.source}")
            print(f"Workspace: {report.clone_root}")
            print(f"Language: {report.detection.language}")
            print(f"Framework: {report.detection.framework}")
            print(f"Bootstrap: {report.bootstrap.success}")
            print(f"Targets: {report.summary.total_targets}")
            print(f"Pass Rate: {report.summary.pass_rate:.2f}%")
            print(f"Average Final Coverage: {report.summary.average_final_coverage:.2f}%")
            print(f"Report: {report.outputs.report_path}")
            print(f"Review: {report.outputs.review_path}")
            print(f"Patch: {report.outputs.patch_path}")
            return
        if not args.target:
            parser.error("run requires --target for local target execution or a repo URL/path subject for repo workflows.")
        orchestrator = ClosedLoopOrchestrator(config)
        result = orchestrator.run_target(args.target)
        print(f"Target: {result.target_id}")
        print(f"Language: {result.language}")
        print(f"Success: {result.final_success}")
        print(f"Coverage: {result.final_coverage:.2f}%")
        print(f"Attempts: {len(result.attempts)}")
        print(f"Stable: {result.stability_passed}")
        if result.attempts:
            last_attempt = result.attempts[-1]
            print(f"Generator: {last_attempt.provider_name or 'heuristic'}")
            if last_attempt.model_name:
                print(f"Model: {last_attempt.model_name}")
            if last_attempt.fallback_reason:
                print(f"Fallback: {last_attempt.fallback_reason}")
        return

    if args.command == "benchmark":
        benchmark_runner = BenchmarkRunner(config)
        spec_path = (config.project_root / args.spec).resolve() if not Path(args.spec).is_absolute() else Path(args.spec)
        report_path = (
            (config.project_root / args.report).resolve() if not Path(args.report).is_absolute() else Path(args.report)
        )
        report = benchmark_runner.run(spec_path, report_path=report_path)
        print(f"Benchmarks: {report.summary.total_targets}")
        print(f"Pass Rate: {report.summary.pass_rate:.2f}%")
        print(f"Average Final Coverage: {report.summary.average_final_coverage:.2f}%")
        print(f"Average Coverage Gain: {report.summary.average_coverage_gain:.2f}%")
        print(f"Fallback Rate: {report.summary.fallback_rate:.2f}%")
        print(f"Invalid Output Rate: {report.summary.invalid_output_rate:.2f}%")
        print(f"Repair Success Rate: {report.summary.repair_success_rate:.2f}%")
        print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
