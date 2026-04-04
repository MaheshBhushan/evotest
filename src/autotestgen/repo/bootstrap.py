from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from autotestgen.config import HarnessConfig
from autotestgen.adapters.go.runner import GoRunner

from .models import BootstrapResult, ProjectDetection


class EnvironmentBootstrapper:
    def __init__(self, config: HarnessConfig) -> None:
        self.config = config

    def bootstrap(
        self,
        project_root: Path,
        detection: ProjectDetection,
        *,
        skip_install: bool = False,
    ) -> BootstrapResult:
        if skip_install or detection.bootstrap_method == "none":
            return BootstrapResult(attempted=False, success=True)
        if detection.language == "mixed":
            return self._bootstrap_mixed(project_root, detection)
        if detection.language == "python":
            return self._bootstrap_python(project_root, detection)
        if detection.language == "javascript":
            return self._bootstrap_javascript(project_root, detection)
        if detection.language == "go":
            return self._bootstrap_go(project_root, detection)
        return BootstrapResult(attempted=False, success=False, error=f"Unsupported language: {detection.language}")

    def _bootstrap_mixed(self, project_root: Path, detection: ProjectDetection) -> BootstrapResult:
        commands: list[str] = []
        python_executable: str | None = None
        for language in detection.languages:
            if language == "python":
                result = self._bootstrap_python(
                    project_root,
                    ProjectDetection(
                        language="python",
                        framework=detection.framework,
                        package_manager="pip",
                        bootstrap_method="requirements.txt" if (project_root / "requirements.txt").exists() else "pyproject.toml" if (project_root / "pyproject.toml").exists() else "none",
                        languages=["python"],
                    ),
                )
            elif language == "javascript":
                package_json = project_root / "package.json"
                js_bootstrap = "none"
                package_manager = "npm"
                if package_json.exists():
                    if (project_root / "pnpm-lock.yaml").exists():
                        package_manager = "pnpm"
                        js_bootstrap = "pnpm install"
                    elif (project_root / "yarn.lock").exists():
                        package_manager = "yarn"
                        js_bootstrap = "yarn install"
                    elif (project_root / "bun.lockb").exists():
                        package_manager = "bun"
                        js_bootstrap = "bun install"
                    else:
                        js_bootstrap = "npm ci" if (project_root / "package-lock.json").exists() else "npm install"
                result = self._bootstrap_javascript(
                    project_root,
                    ProjectDetection(
                        language="javascript",
                        framework=detection.framework,
                        package_manager=package_manager,
                        bootstrap_method=js_bootstrap,
                        languages=["javascript"],
                    ),
                )
            elif language == "go":
                result = self._bootstrap_go(
                    project_root,
                    ProjectDetection(
                        language="go",
                        framework="go test",
                        package_manager="go modules",
                        bootstrap_method="go mod download" if (project_root / "go.mod").exists() else "none",
                        languages=["go"],
                    ),
                )
            else:
                continue
            commands.extend(result.commands)
            python_executable = python_executable or result.python_executable
            if not result.success:
                return BootstrapResult(
                    attempted=True,
                    success=False,
                    commands=commands,
                    python_executable=python_executable,
                    error=result.error,
                )
        return BootstrapResult(attempted=True, success=True, commands=commands, python_executable=python_executable)

    def _bootstrap_python(self, project_root: Path, detection: ProjectDetection) -> BootstrapResult:
        commands: list[str] = []
        venv_root = project_root / ".autotestgen" / "venv"
        self._run([sys.executable, "-m", "venv", str(venv_root)], cwd=project_root, commands=commands)
        python_executable = venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self._run(
            [str(python_executable), "-m", "pip", "install", "pytest", "pytest-cov"],
            cwd=project_root,
            commands=commands,
        )
        if detection.bootstrap_method == "requirements.txt":
            self._run(
                [str(python_executable), "-m", "pip", "install", "-r", "requirements.txt"],
                cwd=project_root,
                commands=commands,
            )
        elif detection.bootstrap_method == "pyproject.toml":
            self._run(
                [str(python_executable), "-m", "pip", "install", "-e", "."],
                cwd=project_root,
                commands=commands,
            )
        return BootstrapResult(
            attempted=True,
            success=True,
            commands=commands,
            python_executable=str(python_executable),
        )

    def _bootstrap_javascript(self, project_root: Path, detection: ProjectDetection) -> BootstrapResult:
        commands: list[str] = []
        if detection.bootstrap_method == "npm ci":
            self._run(["npm", "ci"], cwd=project_root, commands=commands)
        elif detection.bootstrap_method == "npm install":
            self._run(["npm", "install"], cwd=project_root, commands=commands)
        elif detection.bootstrap_method == "pnpm install":
            self._run(["pnpm", "install"], cwd=project_root, commands=commands)
        elif detection.bootstrap_method == "yarn install":
            self._run(["yarn", "install"], cwd=project_root, commands=commands)
        elif detection.bootstrap_method == "bun install":
            self._run(["bun", "install"], cwd=project_root, commands=commands)
        return BootstrapResult(attempted=True, success=True, commands=commands)

    def _bootstrap_go(self, project_root: Path, detection: ProjectDetection) -> BootstrapResult:
        commands: list[str] = []
        go_command = GoRunner._go_command()
        if go_command is None:
            return BootstrapResult(
                attempted=True,
                success=False,
                commands=commands,
                error="Go executable not found for bootstrap.",
            )
        self._run([str(go_command), "mod", "download"], cwd=project_root, commands=commands)
        return BootstrapResult(attempted=True, success=True, commands=commands)

    @staticmethod
    def _run(command: list[str], *, cwd: Path, commands: list[str]) -> None:
        commands.append(" ".join(command))
        completed = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            output = (completed.stdout or "").strip()
            raise RuntimeError(output or f"Command failed: {' '.join(command)}")
