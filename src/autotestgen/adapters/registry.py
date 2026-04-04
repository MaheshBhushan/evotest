from __future__ import annotations

from pathlib import Path

from autotestgen.adapters.base import LanguageAdapter
from autotestgen.config import HarnessConfig


SUPPORTED_LANGUAGES = ("python", "javascript", "go")


def detect_languages(project_root: Path) -> list[str]:
    resolved_root = project_root.resolve()
    detected: list[str] = []
    if (resolved_root / "pyproject.toml").exists() or any(resolved_root.rglob("*.py")):
        detected.append("python")
    if (resolved_root / "package.json").exists():
        detected.append("javascript")
    else:
        for pattern in ("*.js", "*.jsx", "*.ts", "*.tsx", "*.mjs", "*.cjs"):
            if any(resolved_root.rglob(pattern)):
                detected.append("javascript")
                break
    if (resolved_root / "go.mod").exists() or any(resolved_root.rglob("*.go")):
        detected.append("go")
    return detected or ["python"]


def detect_language(project_root: Path) -> str:
    return detect_languages(project_root)[0]


def resolve_language(config: HarnessConfig) -> str:
    if config.language != "auto":
        return config.language
    return detect_language(config.project_root)


def resolve_languages(config: HarnessConfig) -> list[str]:
    if config.language != "auto":
        return [config.language]
    return detect_languages(config.project_root)


def get_adapter_for_language(config: HarnessConfig, language: str) -> LanguageAdapter:
    if language == "python":
        from autotestgen.adapters.python import PythonLanguageAdapter

        return PythonLanguageAdapter(config)
    if language == "javascript":
        from autotestgen.adapters.javascript import JavaScriptLanguageAdapter

        return JavaScriptLanguageAdapter(config)
    if language == "go":
        from autotestgen.adapters.go import GoLanguageAdapter

        return GoLanguageAdapter(config)
    raise ValueError(f"Unsupported language adapter: {language}")


def get_adapter(config: HarnessConfig) -> LanguageAdapter:
    return get_adapter_for_language(config, resolve_language(config))
