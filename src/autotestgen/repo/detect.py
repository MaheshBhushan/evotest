from __future__ import annotations

import json
import tomllib
from pathlib import Path

from autotestgen.adapters.registry import detect_languages

from .models import ProjectDetection


class ProjectDetector:
    def detect(self, project_root: Path, requested_language: str = "auto") -> ProjectDetection:
        if requested_language == "auto":
            languages = detect_languages(project_root)
            if len(languages) > 1:
                return self._mixed_detection(project_root, languages)
            language = languages[0]
        else:
            language = requested_language
            languages = [requested_language]
        if language == "python":
            return self._python_detection(project_root, languages=languages)
        if language == "javascript":
            return self._javascript_detection(project_root, languages=languages)
        if language == "go":
            return self._go_detection(project_root, languages=languages)
        raise ValueError(f"Unsupported language: {language}")

    @staticmethod
    def _python_detection(project_root: Path, *, languages: list[str] | None = None) -> ProjectDetection:
        requirements = project_root / "requirements.txt"
        pyproject = project_root / "pyproject.toml"
        framework = "pytest"
        bootstrap_method = "none"
        if requirements.exists():
            bootstrap_method = "requirements.txt"
        elif pyproject.exists():
            bootstrap_method = "pyproject.toml"
            try:
                payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                payload = {}
            text = json.dumps(payload).lower()
            if "pytest" in text:
                framework = "pytest"
        return ProjectDetection(
            language="python",
            framework=framework,
            package_manager="pip",
            bootstrap_method=bootstrap_method,
            languages=languages or ["python"],
        )

    @staticmethod
    def _javascript_detection(project_root: Path, *, languages: list[str] | None = None) -> ProjectDetection:
        package_json = project_root / "package.json"
        payload: dict[str, object] = {}
        if package_json.exists():
            try:
                payload = json.loads(package_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
        haystack = json.dumps(payload).lower()
        framework = "node:test"
        if "jest" in haystack:
            framework = "jest"
        elif "vitest" in haystack:
            framework = "vitest"
        package_manager = "npm"
        package_manager_field = str(payload.get("packageManager", "")).lower()
        if package_manager_field.startswith("pnpm"):
            package_manager = "pnpm"
        elif package_manager_field.startswith("yarn"):
            package_manager = "yarn"
        elif package_manager_field.startswith("bun"):
            package_manager = "bun"
        elif (project_root / "pnpm-lock.yaml").exists():
            package_manager = "pnpm"
        elif (project_root / "yarn.lock").exists():
            package_manager = "yarn"
        elif (project_root / "bun.lockb").exists():
            package_manager = "bun"
        bootstrap_method = "none"
        if package_json.exists():
            if package_manager == "npm":
                bootstrap_method = "npm ci" if (project_root / "package-lock.json").exists() else "npm install"
            elif package_manager == "pnpm":
                bootstrap_method = "pnpm install"
            elif package_manager == "yarn":
                bootstrap_method = "yarn install"
            elif package_manager == "bun":
                bootstrap_method = "bun install"
        return ProjectDetection(
            language="javascript",
            framework=framework,
            package_manager=package_manager,
            bootstrap_method=bootstrap_method,
            languages=languages or ["javascript"],
        )

    @staticmethod
    def _go_detection(project_root: Path, *, languages: list[str] | None = None) -> ProjectDetection:
        bootstrap_method = "go mod download" if (project_root / "go.mod").exists() else "none"
        return ProjectDetection(
            language="go",
            framework="go test",
            package_manager="go modules",
            bootstrap_method=bootstrap_method,
            languages=languages or ["go"],
        )

    def _mixed_detection(self, project_root: Path, languages: list[str]) -> ProjectDetection:
        frameworks: list[str] = []
        package_managers: list[str] = []
        bootstrap_methods: list[str] = []
        for language in languages:
            if language == "python":
                detection = self._python_detection(project_root, languages=[language])
            elif language == "javascript":
                detection = self._javascript_detection(project_root, languages=[language])
            elif language == "go":
                detection = self._go_detection(project_root, languages=[language])
            else:
                continue
            frameworks.append(detection.framework)
            package_managers.append(detection.package_manager)
            if detection.bootstrap_method != "none":
                bootstrap_methods.append(detection.bootstrap_method)
        framework = ", ".join(frameworks) if frameworks else "mixed"
        package_manager = ", ".join(package_managers) if package_managers else "mixed"
        bootstrap_method = ", ".join(bootstrap_methods) if bootstrap_methods else "none"
        return ProjectDetection(
            language="mixed",
            framework=framework,
            package_manager=package_manager,
            bootstrap_method=bootstrap_method,
            languages=languages,
        )
