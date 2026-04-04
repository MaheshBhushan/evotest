from pathlib import Path

from autotestgen.analyzer import PythonProjectAnalyzer


def test_discovery_finds_functions_and_skips_tests(tmp_path: Path) -> None:
    package_dir = tmp_path / "pkg"
    tests_dir = tmp_path / "tests"
    package_dir.mkdir()
    tests_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "maths.py").write_text(
        "import math\n\n\ndef add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    (tests_dir / "test_maths.py").write_text(
        "def test_placeholder():\n    assert True\n",
        encoding="utf-8",
    )

    inventory = PythonProjectAnalyzer(tmp_path).discover()

    assert sorted(module.target_id for module in inventory.modules) == ["pkg/__init__.py", "pkg/maths.py"]
    assert inventory.modules[0].target_id == "pkg/maths.py"
    assert [function.target_id for function in inventory.functions] == ["pkg/maths.py::add"]
    assert inventory.functions[0].module_name == "pkg.maths"
    assert inventory.functions[0].target_score >= inventory.functions[0].complexity_score


def test_discovery_normalizes_src_layout_imports(tmp_path: Path) -> None:
    src_dir = tmp_path / "src" / "demo"
    src_dir.mkdir(parents=True)
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "maths.py").write_text(
        "def add(a, b):\n    return a + b\n",
        encoding="utf-8",
    )

    inventory = PythonProjectAnalyzer(tmp_path).discover()

    assert inventory.functions[0].target_id == "src/demo/maths.py::add"
    assert inventory.functions[0].module_name == "demo.maths"


def test_discovery_captures_method_metadata(tmp_path: Path) -> None:
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "service.py").write_text(
        "class Greeter:\n"
        "    def __init__(self, prefix):\n"
        "        self.prefix = prefix\n\n"
        "    def greet(self, name):\n"
        "        return f'{self.prefix} {name}'\n\n"
        "    @classmethod\n"
        "    def loud(cls, name):\n"
        "        return name.upper()\n",
        encoding="utf-8",
    )

    inventory = PythonProjectAnalyzer(tmp_path).discover()

    greet_target = next(function for function in inventory.functions if function.qualname == "Greeter.greet")
    loud_target = next(function for function in inventory.functions if function.qualname == "Greeter.loud")
    assert greet_target.method_kind == "instance"
    assert greet_target.owner_qualname == "Greeter"
    assert greet_target.constructor_parameters == ["prefix"]
    assert loud_target.method_kind == "class"
    assert loud_target.owner_qualname == "Greeter"


def test_discovery_skips_nested_local_functions(tmp_path: Path) -> None:
    (tmp_path / "sample.py").write_text(
        "def outer():\n"
        "    def inner():\n"
        "        return 1\n"
        "    return inner()\n",
        encoding="utf-8",
    )

    inventory = PythonProjectAnalyzer(tmp_path).discover()

    assert [function.target_id for function in inventory.functions] == ["sample.py::outer"]
