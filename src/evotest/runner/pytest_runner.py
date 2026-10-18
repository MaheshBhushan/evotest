from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from evotest.config import HarnessConfig
from evotest.models import CoverageResult, ExecutionResult, FunctionTarget, ModuleTarget
from evotest.runner.mutmut_runner import MutmutRunner


class PytestRunner:
    def __init__(self, config: HarnessConfig) -> None:
        self.config = config
        self.python_executable = config.python_executable or sys.executable

    def run(
        self,
        target: FunctionTarget | ModuleTarget,
        test_source: str,
        test_file_path: Path,
        attempt_number: int,
    ) -> ExecutionResult:
        runtime_root = self.config.artifacts_dir / "runs"
        runtime_root.mkdir(parents=True, exist_ok=True)
        workspace_root = runtime_root / f"attempt_{attempt_number}_{uuid4().hex[:8]}"
        workspace_root.mkdir(parents=True, exist_ok=True)
        try:
            project_copy = workspace_root / "project"
            self._copy_project(project_copy)
            self._write_sitecustomize(project_copy)
            generated_dir = project_copy / ".evotest" / "generated_tests"
            generated_dir.mkdir(parents=True, exist_ok=True)
            runtime_dir = project_copy / ".evotest" / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            test_path_in_workspace = generated_dir / test_file_path.name
            test_path_in_workspace.write_text(test_source, encoding="utf-8")
            coverage_path = project_copy / ".evotest" / f"coverage_{attempt_number}.json"
            env = self._sandbox_env(project_copy, runtime_dir)
            command = [
                self.python_executable,
                "-m",
                self.config.pytest_command,
                str(test_path_in_workspace.relative_to(project_copy).as_posix()),
                "--maxfail=1",
                "--disable-warnings",
                "-p",
                "pytest_cov",
                "-p",
                "no:cacheprovider",
                "--basetemp",
                str(runtime_dir / "pytest"),
                f"--cov={target.module_name}",
                "--cov-report",
                f"json:{coverage_path}",
                "-q",
            ]
            start = time.perf_counter()
            process = subprocess.Popen(
                command,
                cwd=project_copy,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                start_new_session=os.name != "nt",
            )
            try:
                stdout, stderr = process.communicate(timeout=self.config.max_runtime_seconds)
                duration = time.perf_counter() - start
                coverage = self._read_coverage(
                    coverage_path=coverage_path,
                    copied_project_root=project_copy,
                    target=target,
                )
                mutation = None
                if process.returncode == 0 and self.config.mutation_testing and attempt_number < 1000:
                    mutation = MutmutRunner(self.config, self.python_executable).run(
                        project_copy, target, test_path_in_workspace, env,
                    )
                return ExecutionResult(
                    target_id=target.target_id,
                    attempt_number=attempt_number,
                    success=process.returncode == 0,
                    exit_code=int(process.returncode or 0),
                    stdout=stdout,
                    stderr=stderr,
                    duration_seconds=duration,
                    coverage=coverage,
                    test_file_path=test_file_path,
                    mutation=mutation,
                )
            except subprocess.TimeoutExpired:
                self._terminate_process_tree(process)
                stdout, stderr = process.communicate()
                duration = time.perf_counter() - start
                return ExecutionResult(
                    target_id=target.target_id,
                    attempt_number=attempt_number,
                    success=False,
                    exit_code=124,
                    stdout=stdout or "",
                    stderr=stderr or "",
                    duration_seconds=duration,
                    coverage=None,
                    test_file_path=test_file_path,
                )
        finally:
            self._cleanup_workspace(workspace_root)

    def rerun_for_stability(self, target: FunctionTarget | ModuleTarget, test_source: str, test_file_path: Path) -> bool:
        for attempt in range(self.config.stability_reruns):
            result = self.run(target, test_source, test_file_path, attempt_number=1000 + attempt)
            if not result.success:
                return False
        return True

    def _copy_project(self, destination: Path) -> None:
        shutil.copytree(
            self.config.project_root,
            destination,
            ignore=shutil.ignore_patterns(
                ".git",
                ".evotest",
                "__pycache__",
                ".pytest_cache",
                ".tmp",
                "htmlcov",
                ".coverage",
                ".venv",
                "venv",
                "node_modules",
                "mutants",
                ".mutmut-cache",
            ),
        )

    def _cleanup_workspace(self, workspace_root: Path) -> None:
        shutil.rmtree(workspace_root, ignore_errors=True)

    def _write_sitecustomize(self, project_copy: Path) -> None:
        (project_copy / "sitecustomize.py").write_text(
            "from _pytest import pathlib as pytest_pathlib\n"
            "from _pytest import tmpdir as pytest_tmpdir\n\n"
            "original = pytest_pathlib.cleanup_dead_symlinks\n\n"
            "def safe_cleanup_dead_symlinks(root):\n"
            "    try:\n"
            "        original(root)\n"
            "    except PermissionError:\n"
            "        return\n\n"
            "pytest_pathlib.cleanup_dead_symlinks = safe_cleanup_dead_symlinks\n"
            "pytest_tmpdir.cleanup_dead_symlinks = safe_cleanup_dead_symlinks\n",
            encoding="utf-8",
        )

    def _sandbox_env(self, project_copy: Path, runtime_dir: Path) -> dict[str, str]:
        env = os.environ.copy()
        python_path_entries = [str(project_copy)]
        src_dir = project_copy / "src"
        if src_dir.exists():
            python_path_entries.append(str(src_dir))
        env["PYTHONPATH"] = os.pathsep.join(python_path_entries)
        env["TMP"] = str(runtime_dir)
        env["TEMP"] = str(runtime_dir)
        env["TMPDIR"] = str(runtime_dir)
        env["HOME"] = str(runtime_dir)
        env["USERPROFILE"] = str(runtime_dir)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        env["NO_PROXY"] = "*"
        env["http_proxy"] = ""
        env["https_proxy"] = ""
        return env

    def _terminate_process_tree(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except OSError:
            process.kill()

    def _read_coverage(
        self,
        *,
        coverage_path: Path,
        copied_project_root: Path,
        target: FunctionTarget | ModuleTarget,
    ) -> CoverageResult | None:
        if not coverage_path.exists():
            return None
        try:
            payload = json.loads(coverage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        target_relative = target.file_path.relative_to(self.config.project_root)
        copied_target_path = (copied_project_root / target_relative).resolve()
        modules = payload.get("files", {})
        for file_name, file_payload in modules.items():
            file_path = (copied_project_root / file_name).resolve()
            if file_path == copied_target_path:
                summary = file_payload.get("summary", {})
                return CoverageResult(
                    statements=int(summary.get("num_statements", 0)),
                    missing=len(file_payload.get("missing_lines", [])),
                    coverage_percent=float(summary.get("percent_covered", 0.0)),
                    missing_lines=list(file_payload.get("missing_lines", [])),
                )
        totals = payload.get("totals", {})
        if not totals:
            return None
        return CoverageResult(
            statements=int(totals.get("num_statements", 0)),
            missing=int(totals.get("missing_lines", 0)),
            coverage_percent=float(totals.get("percent_covered", 0.0)),
            missing_lines=[],
        )
