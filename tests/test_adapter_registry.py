from pathlib import Path

from autotestgen.adapters.registry import detect_language, detect_languages, get_adapter
from autotestgen.config import HarnessConfig


def test_adapter_registry_returns_python_adapter(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        language="python",
    )

    adapter = get_adapter(config)

    assert adapter.language_id == "python"
    assert adapter.adapter_id == "python"


def test_adapter_registry_auto_detects_javascript(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"demo","devDependencies":{"vitest":"^1.0.0"}}', encoding="utf-8")

    assert detect_language(tmp_path) == "javascript"


def test_adapter_registry_auto_detects_go(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")

    assert detect_language(tmp_path) == "go"


def test_adapter_registry_auto_detects_file_only_go_repo(tmp_path: Path) -> None:
    (tmp_path / "adder.go").write_text(
        "package main\n\n"
        "func Add(a int, b int) int { return a + b }\n",
        encoding="utf-8",
    )

    assert detect_language(tmp_path) == "go"


def test_adapter_registry_returns_go_adapter(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.22\n", encoding="utf-8")
    config = HarnessConfig(
        project_root=tmp_path,
        database_path=tmp_path / ".autotestgen" / "state.db",
        language="go",
    )

    adapter = get_adapter(config)

    assert adapter.language_id == "go"
    assert adapter.adapter_id == "go"


def test_adapter_registry_auto_detects_polyglot_repo(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name":"demo"}', encoding="utf-8")

    assert detect_languages(tmp_path) == ["python", "javascript"]
    assert detect_language(tmp_path) == "python"
