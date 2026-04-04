from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4

from evotest.config import HarnessConfig
from evotest.models import CoverageResult, ExecutionResult, FunctionTarget, ModuleTarget


class JavaScriptRunner:
    language_id = "javascript"
    adapter_id = "javascript"

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
            self._attach_dependency_roots(project_copy)
            generated_dir = project_copy / ".evotest" / "generated_tests"
            generated_dir.mkdir(parents=True, exist_ok=True)
            runtime_dir = project_copy / ".evotest" / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            coverage_dir = runtime_dir / "coverage"
            test_path_in_workspace, command, coverage_mode = self._prepare_execution(
                project_copy=project_copy,
                generated_dir=generated_dir,
                runtime_dir=runtime_dir,
                test_source=test_source,
                test_file_path=test_file_path,
                coverage_dir=coverage_dir,
            )
            start = time.perf_counter()
            process = subprocess.Popen(
                command,
                cwd=project_copy,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._sandbox_env(runtime_dir, v8_coverage_dir=coverage_dir if coverage_mode == "v8" else None),
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            try:
                stdout, stderr = process.communicate(timeout=self.config.max_runtime_seconds)
                duration = time.perf_counter() - start
                coverage = self._read_coverage(coverage_dir=coverage_dir, copied_project_root=project_copy, target=target)
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
                    language=self.language_id,
                    adapter_id=self.adapter_id,
                )
        finally:
            shutil.rmtree(workspace_root, ignore_errors=True)

    def _prepare_execution(
        self,
        *,
        project_copy: Path,
        generated_dir: Path,
        runtime_dir: Path,
        test_source: str,
        test_file_path: Path,
        coverage_dir: Path,
    ) -> tuple[Path, list[str], str]:
        test_path_in_workspace = generated_dir / test_file_path.name
        test_path_in_workspace.write_text(test_source, encoding="utf-8")
        local_command = self._local_framework_command(project_copy, test_path_in_workspace, coverage_dir)
        if local_command is not None:
            return test_path_in_workspace, local_command, "summary"
        shim_path = generated_dir / "evotest_vitest_shim.mjs"
        shim_path.write_text(self._zero_install_shim_source(), encoding="utf-8")
        shim_test_path = generated_dir / f"{test_file_path.stem}_zero_install.mjs"
        shim_test_path.write_text(self._rewrite_for_zero_install(test_source), encoding="utf-8")
        return shim_test_path, ["node", str(shim_test_path)], "v8"

    def _local_framework_command(self, project_copy: Path, test_file: Path, coverage_dir: Path) -> list[str] | None:
        framework = self._preferred_framework(project_copy)
        binary = self._local_framework_binary(project_copy, framework)
        if binary is None:
            return None
        relative_test = test_file.relative_to(project_copy).as_posix()
        if framework == "jest":
            command = [
                relative_test,
                "--runInBand",
                "--coverage",
                "--coverageReporters=json-summary",
                "--coverageDirectory",
                str(coverage_dir),
            ]
            return self._wrap_binary_command(binary, command)
        command = [
            "run",
            relative_test,
            "--coverage.enabled",
            "true",
            "--coverage.reporter",
            "json-summary",
            "--coverage.reportsDirectory",
            str(coverage_dir),
        ]
        return self._wrap_binary_command(binary, command)

    def _preferred_framework(self, project_copy: Path) -> str:
        package_json = project_copy / "package.json"
        framework = "vitest"
        if package_json.exists():
            try:
                payload = json.loads(package_json.read_text(encoding="utf-8"))
                haystack = json.dumps(payload).lower()
                if "jest" in haystack:
                    framework = "jest"
            except (OSError, json.JSONDecodeError):
                framework = "vitest"
        return framework

    @staticmethod
    def _local_framework_binary(project_copy: Path, framework: str) -> Path | None:
        bin_dir = project_copy / "node_modules" / ".bin"
        candidates = [bin_dir / framework]
        if os.name == "nt":
            candidates = [bin_dir / f"{framework}.cmd", bin_dir / f"{framework}.bat", *candidates]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _wrap_binary_command(binary: Path, args: list[str]) -> list[str]:
        if binary.suffix.lower() in {".cmd", ".bat"}:
            return ["cmd", "/c", str(binary), *args]
        return [str(binary), *args]

    def _copy_project(self, destination: Path) -> None:
        shutil.copytree(
            self.config.project_root,
            destination,
            ignore=shutil.ignore_patterns(".git", ".evotest", "node_modules", "coverage"),
        )

    def _attach_dependency_roots(self, destination: Path) -> None:
        source_node_modules = self.config.project_root / "node_modules"
        if not source_node_modules.exists():
            return
        target_node_modules = destination / "node_modules"
        if target_node_modules.exists():
            return
        try:
            os.symlink(source_node_modules, target_node_modules, target_is_directory=True)
            return
        except OSError:
            pass
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(target_node_modules), str(source_node_modules)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode == 0:
                return
        shutil.copytree(source_node_modules, target_node_modules, ignore=shutil.ignore_patterns(".cache"))

    def _sandbox_env(self, runtime_dir: Path, *, v8_coverage_dir: Path | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["TMP"] = str(runtime_dir)
        env["TEMP"] = str(runtime_dir)
        env["TMPDIR"] = str(runtime_dir)
        env["HOME"] = str(runtime_dir)
        env["USERPROFILE"] = str(runtime_dir)
        source_node_modules = self.config.project_root / "node_modules"
        if source_node_modules.exists():
            existing_node_path = env.get("NODE_PATH", "")
            env["NODE_PATH"] = os.pathsep.join(part for part in [str(source_node_modules), existing_node_path] if part)
        if v8_coverage_dir is not None:
            env["NODE_V8_COVERAGE"] = str(v8_coverage_dir)
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
        coverage_dir: Path,
        copied_project_root: Path,
        target: FunctionTarget | ModuleTarget,
    ) -> CoverageResult | None:
        summary_path = coverage_dir / "coverage-summary.json"
        if summary_path.exists():
            summary_coverage = self._read_summary_coverage(summary_path=summary_path, target=target)
            if summary_coverage is not None:
                return summary_coverage
        return self._read_v8_coverage(
            coverage_dir=coverage_dir,
            copied_project_root=copied_project_root,
            target=target,
        )

    def _read_summary_coverage(
        self,
        *,
        summary_path: Path,
        target: FunctionTarget | ModuleTarget,
    ) -> CoverageResult | None:
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        target_relative = target.file_path.relative_to(self.config.project_root).as_posix()
        target_key_suffix = target_relative.replace("\\", "/")
        for file_name, file_payload in payload.items():
            if file_name == "total":
                continue
            normalized = file_name.replace("\\", "/")
            if normalized.endswith(target_key_suffix):
                lines = file_payload.get("lines", {})
                statements = int(lines.get("total", 0))
                covered = int(lines.get("covered", 0))
                missing = max(statements - covered, 0)
                percent = float(lines.get("pct", 0.0))
                return CoverageResult(
                    statements=statements,
                    missing=missing,
                    coverage_percent=percent,
                    missing_lines=[],
                )
        totals = payload.get("total")
        if not totals:
            return None
        lines = totals.get("lines", {})
        statements = int(lines.get("total", 0))
        covered = int(lines.get("covered", 0))
        missing = max(statements - covered, 0)
        return CoverageResult(
            statements=statements,
            missing=missing,
            coverage_percent=float(lines.get("pct", 0.0)),
            missing_lines=[],
        )

    def _read_v8_coverage(
        self,
        *,
        coverage_dir: Path,
        copied_project_root: Path,
        target: FunctionTarget | ModuleTarget,
    ) -> CoverageResult | None:
        copied_target_path = copied_project_root / target.file_path.relative_to(self.config.project_root)
        try:
            source = copied_target_path.read_text(encoding="utf-8")
        except OSError:
            return None
        executable_lines = self._executable_lines(source)
        if not executable_lines:
            return None
        covered_lines: set[int] = set()
        line_offsets = self._line_offsets(source)
        covered_ranges = self._covered_ranges_for_target(coverage_dir=coverage_dir, target_path=copied_target_path)
        for line_number in executable_lines:
            start_offset, end_offset = line_offsets[line_number - 1]
            if any(start_offset < range_end and end_offset > range_start for range_start, range_end in covered_ranges):
                covered_lines.add(line_number)
        statements = len(executable_lines)
        covered = len(covered_lines)
        missing_lines = sorted(set(executable_lines) - covered_lines)
        return CoverageResult(
            statements=statements,
            missing=statements - covered,
            coverage_percent=round((covered / statements) * 100.0, 2),
            missing_lines=missing_lines,
        )

    @staticmethod
    def _line_offsets(source: str) -> list[tuple[int, int]]:
        offsets: list[tuple[int, int]] = []
        cursor = 0
        for line in source.splitlines(keepends=True):
            start = cursor
            cursor += len(line)
            offsets.append((start, cursor))
        if source and not source.endswith(("\n", "\r")):
            return offsets
        if not offsets and source == "":
            return []
        return offsets

    @staticmethod
    def _executable_lines(source: str) -> list[int]:
        executable: list[int] = []
        for line_number, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            if stripped in {"{", "}", "};", "});"}:
                continue
            executable.append(line_number)
        return executable

    def _covered_ranges_for_target(self, *, coverage_dir: Path, target_path: Path) -> list[tuple[int, int]]:
        covered_ranges: list[tuple[int, int]] = []
        for payload_path in coverage_dir.glob("*.json"):
            try:
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for entry in payload.get("result", []):
                script_path = self._coverage_script_path(entry.get("url", ""))
                if script_path is None or script_path.resolve() != target_path.resolve():
                    continue
                for function_payload in entry.get("functions", []):
                    for range_payload in function_payload.get("ranges", []):
                        if int(range_payload.get("count", 0)) <= 0:
                            continue
                        covered_ranges.append(
                            (
                                int(range_payload.get("startOffset", 0)),
                                int(range_payload.get("endOffset", 0)),
                            )
                        )
        return covered_ranges

    @staticmethod
    def _coverage_script_path(url: str) -> Path | None:
        if not url.startswith("file:"):
            return None
        parsed = urlparse(url)
        decoded = unquote(parsed.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", decoded):
            decoded = decoded[1:]
        return Path(decoded)

    @staticmethod
    def _rewrite_for_zero_install(test_source: str) -> str:
        stripped_source = re.sub(
            r"(?m)^\s*import\s*\{[^}]*\}\s*from\s*['\"](?:vitest|@jest/globals)['\"];\s*\n?",
            "",
            test_source,
        )
        stripped_source = re.sub(
            r"(?m)^\s*const\s*\{[^}]*\}\s*=\s*require\(\s*['\"](?:vitest|@jest/globals)['\"]\s*\);\s*\n?",
            "",
            stripped_source,
        )
        return (
            "import { beforeAll, describe, it, test, expect, __evotestRun } "
            "from './evotest_vitest_shim.mjs';\n"
            f"{stripped_source.rstrip()}\n\n"
            "await __evotestRun();\n"
        )

    @staticmethod
    def _zero_install_shim_source() -> str:
        return (
            "import assert from 'node:assert/strict';\n\n"
            "const beforeAllHooks = [];\n"
            "const registeredTests = [];\n\n"
            "export function beforeAll(fn) {\n"
            "  beforeAllHooks.push(fn);\n"
            "}\n\n"
            "export function describe(_name, fn) {\n"
            "  fn();\n"
            "}\n\n"
            "export function test(name, fn) {\n"
            "  registeredTests.push({ name, fn });\n"
            "}\n\n"
            "export const it = test;\n\n"
            "function escapeRegExp(value) {\n"
            "  return value.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');\n"
            "}\n\n"
            "export function expect(actual) {\n"
            "  return {\n"
            "    toBe(expected) {\n"
            "      assert.strictEqual(actual, expected);\n"
            "    },\n"
            "    toEqual(expected) {\n"
            "      assert.deepStrictEqual(actual, expected);\n"
            "    },\n"
            "    toStrictEqual(expected) {\n"
            "      assert.deepStrictEqual(actual, expected);\n"
            "    },\n"
            "    toContain(expected) {\n"
            "      if (typeof actual === 'string') {\n"
            "        assert.ok(actual.includes(expected));\n"
            "        return;\n"
            "      }\n"
            "      assert.ok(Array.isArray(actual) && actual.includes(expected));\n"
            "    },\n"
            "    toThrow(expected) {\n"
            "      assert.equal(typeof actual, 'function');\n"
            "      let thrown = null;\n"
            "      try {\n"
            "        actual();\n"
            "      } catch (error) {\n"
            "        thrown = error;\n"
            "      }\n"
            "      assert.notStrictEqual(thrown, null);\n"
            "      if (expected instanceof RegExp) {\n"
            "        assert.match(String(thrown), expected);\n"
            "      } else if (typeof expected === 'string') {\n"
            "        assert.match(String(thrown), new RegExp(escapeRegExp(expected)));\n"
            "      }\n"
            "    },\n"
            "    toMatch(expected) {\n"
            "      const matcher = expected instanceof RegExp ? expected : new RegExp(escapeRegExp(String(expected)));\n"
            "      assert.match(String(actual), matcher);\n"
            "    },\n"
            "    toBeDefined() {\n"
            "      assert.notStrictEqual(actual, undefined);\n"
            "    },\n"
            "    toBeUndefined() {\n"
            "      assert.strictEqual(actual, undefined);\n"
            "    },\n"
            "    not: {\n"
            "      toBeUndefined() {\n"
            "        assert.notStrictEqual(actual, undefined);\n"
            "      },\n"
            "      toBe(expected) {\n"
            "        assert.notStrictEqual(actual, expected);\n"
            "      },\n"
            "      toBeNull() {\n"
            "        assert.notStrictEqual(actual, null);\n"
            "      }\n"
            "    }\n"
            "  };\n"
            "}\n\n"
            "export async function __evotestRun() {\n"
            "  for (const hook of beforeAllHooks) {\n"
            "    await hook();\n"
            "  }\n"
            "  if (registeredTests.length === 0) {\n"
            "    throw new Error('No tests were registered.');\n"
            "  }\n"
            "  for (const entry of registeredTests) {\n"
            "    try {\n"
            "      await entry.fn();\n"
            "      process.stdout.write(`ok ${entry.name}\\n`);\n"
            "    } catch (error) {\n"
            "      process.stderr.write(`Test failed: ${entry.name}\\n${error?.stack || error}\\n`);\n"
            "      process.exitCode = 1;\n"
            "    }\n"
            "  }\n"
            "}\n"
        )
