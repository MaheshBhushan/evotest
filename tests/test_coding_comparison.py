import json
import sys
from pathlib import Path

from evotest.coding_comparison import CodingComparisonRunner


def test_coding_comparison_uses_same_acceptance_tests_and_separates_outputs(tmp_path: Path) -> None:
    project = tmp_path / "task"
    project.mkdir()
    (project / "solution.py").write_text("def answer(): return 0\n", encoding="utf-8")
    spec_path = tmp_path / "comparison.json"
    fix = "from pathlib import Path; Path('solution.py').write_text('def answer(): return 42\\n')"
    repair = (
        "import sys; from pathlib import Path; prompt=sys.stdin.read(); "
        "Path('solution.py').write_text('def answer(): return 42\\n') if 'Acceptance tests failed' in prompt else None; "
        "print('tool output')"
    )
    coverage_write = "from pathlib import Path; Path('coverage.json').write_text('{\"totals\":{\"percent_covered\":75}}')"
    spec_path.write_text(json.dumps({
        "max_repairs": 1,
        "tasks": [{"name": "answer", "project_root": "task", "prompt": "Implement answer.",
                   "acceptance_command": [sys.executable, "-c", "from solution import answer; assert answer() == 42"],
                   "coverage_file": "coverage.json"}],
        "configurations": [
            {"name": "tool-a", "command": [sys.executable, "-c", f"{fix}; {coverage_write}; print('a')"]},
            {"name": "tool-b", "command": [sys.executable, "-c", f"{repair}; {coverage_write}"]},
        ],
    }), encoding="utf-8")

    report = CodingComparisonRunner().run(spec_path, report_path=tmp_path / "report.json")

    first, second = report["results"]
    assert first["functional_correctness"] is True
    assert first["repair_attempts"] == 0
    assert first["coverage_percent"] == 75.0
    assert second["functional_correctness"] is True
    assert second["repair_attempts"] == 1
    assert second["tool_outputs"][0]["stdout"] == "tool output\n"
    assert second["reference_test_results"][0]["exit_code"] != 0
    assert second["reference_test_results"][1]["exit_code"] == 0
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == report
