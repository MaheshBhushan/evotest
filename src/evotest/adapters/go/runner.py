from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from evotest.config import HarnessConfig
from evotest.models import CoverageResult, ExecutionResult, FunctionTarget, ModuleTarget


class GoRunner:
    language_id = "go"
    adapter_id = "go"
    generated_package_dir = "evotest_generated_tests"

    def __init__(self, config: HarnessConfig) -> None:
        self.config = config

    def run(
        self,
        *,
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
            generated_dir = project_copy / self.generated_package_dir
            generated_dir.mkdir(parents=True, exist_ok=True)
            runtime_dir = project_copy / "evotest_runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            test_path_in_workspace = generated_dir / test_file_path.name
            test_path_in_workspace.write_text(test_source, encoding="utf-8")
            coverage_path = runtime_dir / f"coverage_{attempt_number}.out"
            go_command = self._go_command()
            if go_command is None:
                return ExecutionResult(
                    target_id=target.target_id,
                    attempt_number=attempt_number,
                    success=False,
                    exit_code=127,
                    stdout="",
                    stderr="Go executable not found. Set EVOTEST_GO_BIN or install Go in a standard location.",
                    duration_seconds=0.0,
                    coverage=None,
                    test_file_path=test_file_path,
                    language=self.language_id,
                    adapter_id=self.adapter_id,
                )
            base_command = [
                str(go_command),
                "test",
                f"./{self.generated_package_dir}",
                "-count=1",
                f"-coverpkg={target.module_name}",
                f"-coverprofile={coverage_path}",
            ]
            command = ["cmd", "/c", *base_command] if os.name == "nt" else base_command
            start = time.perf_counter()
            process = subprocess.Popen(
                command,
                cwd=project_copy,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._sandbox_env(runtime_dir),
            )
            try:
                stdout, stderr = process.communicate(timeout=self.config.max_runtime_seconds)
                duration = time.perf_counter() - start
                coverage = self._read_coverage(
                    coverage_path=coverage_path,
                    copied_project_root=project_copy,
                    target=target,
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
                    language=self.language_id,
                    adapter_id=self.adapter_id,
                )
            except subprocess.TimeoutExpired:
                self._terminate_process_tree(process)
                stdout, stderr = process.communicate()
                duration = time.perf_counter() - start
                coverage = self._read_coverage(
                    coverage_path=coverage_path,
                    copied_project_root=project_copy,
                    target=target,
                )
                if self._timed_out_but_completed(stdout=stdout or "", stderr=stderr or "", coverage=coverage):
                    return ExecutionResult(
                        target_id=target.target_id,
                        attempt_number=attempt_number,
                        success=True,
                        exit_code=0,
                        stdout=stdout or "",
                        stderr=stderr or "",
                        duration_seconds=duration,
                        coverage=coverage,
                        test_file_path=test_file_path,
                        language=self.language_id,
                        adapter_id=self.adapter_id,
                    )
                return ExecutionResult(
                    target_id=target.target_id,
                    attempt_number=attempt_number,
                    success=False,
                    exit_code=124,
                    stdout=stdout or "",
                    stderr=stderr or "",
                    duration_seconds=duration,
                    coverage=coverage,
                    test_file_path=test_file_path,
                    language=self.language_id,
                    adapter_id=self.adapter_id,
                )
        finally:
            shutil.rmtree(workspace_root, ignore_errors=True)

    @staticmethod
    def _go_command() -> Path | None:
        env_override = os.getenv("EVOTEST_GO_BIN")
        candidates: list[Path] = []
        if env_override:
            candidates.append(Path(env_override))
        which_go = shutil.which("go")
        if which_go:
            candidates.append(Path(which_go))
        if os.name == "nt":
            candidates.extend(
                [
                    Path(r"C:\Program Files\Go\bin\go.exe"),
                    Path(r"C:\Go\bin\go.exe"),
                ]
            )
        else:
            candidates.extend(
                [
                    Path("/usr/local/go/bin/go"),
                    Path("/opt/homebrew/bin/go"),
                    Path("/usr/bin/go"),
                ]
            )
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _copy_project(self, destination: Path) -> None:
        shutil.copytree(
            self.config.project_root,
            destination,
            ignore=shutil.ignore_patterns(".git", ".evotest", ".tmp", "__pycache__", "coverage"),
        )

    @staticmethod
    def _sandbox_env(runtime_dir: Path) -> dict[str, str]:
        gopath = runtime_dir / "gopath"
        gocache = runtime_dir / "gocache"
        gopath.mkdir(parents=True, exist_ok=True)
        gocache.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["TMP"] = str(runtime_dir)
        env["TEMP"] = str(runtime_dir)
        env["TMPDIR"] = str(runtime_dir)
        env["HOME"] = str(runtime_dir)
        env["USERPROFILE"] = str(runtime_dir)
        env["GOCACHE"] = str(gocache)
        env["GOPATH"] = str(gopath)
        env["GOMODCACHE"] = str(gopath / "pkg" / "mod")
        env["GOTMPDIR"] = str(runtime_dir)
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
            lines = coverage_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        target_relative = target.file_path.relative_to(self.config.project_root).as_posix()
        statements = 0
        covered = 0
        missing_lines: list[int] = []
        for raw_line in lines[1:]:
            line = raw_line.strip()
            if not line:
                continue
            try:
                file_part, range_part, stmt_count, hit_count = self._parse_cover_line(line)
            except ValueError:
                continue
            normalized_file = file_part.replace("\\", "/")
            if not normalized_file.endswith(target_relative):
                continue
            statements += stmt_count
            if hit_count > 0:
                covered += stmt_count
            else:
                missing_lines.append(int(range_part.split(".", 1)[0]))
        if statements == 0:
            return None
        missing = statements - covered
        return CoverageResult(
            statements=statements,
            missing=missing,
            coverage_percent=round((covered / statements) * 100.0, 2),
            missing_lines=missing_lines,
        )

    @staticmethod
    def _parse_cover_line(line: str) -> tuple[str, str, int, int]:
        file_part, remainder = line.split(":", 1)
        range_part, stmt_count, hit_count = remainder.rsplit(" ", 2)
        return file_part, range_part.split(",", 1)[0], int(stmt_count), int(hit_count)

    @staticmethod
    def _timed_out_but_completed(stdout: str, stderr: str, coverage: CoverageResult | None) -> bool:
        if coverage is None or stderr.strip():
            return False
        return re.search(r"(?m)^ok\s", stdout) is not None
