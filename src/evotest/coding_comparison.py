"""Controlled comparisons of command-line coding tools on fixed tasks."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class CodingComparisonRunner:
    """Run exactly two coding configurations against isolated copies of each task."""

    def run(self, spec_path: Path, *, report_path: Path | None = None) -> dict[str, Any]:
        spec_path = spec_path.resolve()
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        configs = spec["configurations"]
        tasks = spec["tasks"]
        if len(configs) != 2 or not tasks:
            raise ValueError("comparison specs require exactly two configurations and at least one task")
        max_repairs = int(spec.get("max_repairs", 0))
        if max_repairs < 0:
            raise ValueError("max_repairs must be zero or greater")
        root = (spec_path.parent / spec.get("workspace_root", ".evotest/coding_comparisons")).resolve()
        root = root / uuid4().hex
        comparisons = []
        for task_index, task in enumerate(tasks, 1):
            task_root = self._resolve(spec_path.parent, task["project_root"])
            for config_index, config in enumerate(configs, 1):
                workspace = root / f"{task_index}-{task['name']}" / f"{config_index}-{config['name']}"
                shutil.copytree(task_root, workspace)
                prompt = str(task["prompt"])
                tool_runs = []
                acceptance_runs = []
                for attempt in range(max_repairs + 1):
                    result = subprocess.run(
                        [str(arg).replace("{workspace}", str(workspace)) for arg in config["command"]],
                        cwd=workspace,
                        input=prompt,
                        text=True,
                        capture_output=True,
                        timeout=int(spec.get("tool_timeout_seconds", 300)),
                        check=False,
                    )
                    tool_runs.append(
                        {
                            "attempt": attempt + 1,
                            "exit_code": result.returncode,
                            "stdout": result.stdout,
                            "stderr": result.stderr,
                        }
                    )
                    test = subprocess.run(
                        task["acceptance_command"],
                        cwd=workspace,
                        text=True,
                        capture_output=True,
                        timeout=int(spec.get("test_timeout_seconds", 120)),
                        check=False,
                    )
                    acceptance = {
                        "attempt": attempt + 1,
                        "exit_code": test.returncode,
                        "stdout": test.stdout,
                        "stderr": test.stderr,
                        "coverage_percent": self._coverage(workspace, task.get("coverage_file")),
                    }
                    acceptance_runs.append(acceptance)
                    if test.returncode == 0:
                        break
                    prompt = (
                        str(task["prompt"])
                        + "\n\nAcceptance tests failed. Repair the implementation.\n"
                        + test.stdout
                        + "\n"
                        + test.stderr
                    )
                final = acceptance_runs[-1]
                comparisons.append(
                    {
                        "task": task["name"],
                        "configuration": config["name"],
                        "functional_correctness": final["exit_code"] == 0,
                        "coverage_percent": final["coverage_percent"],
                        "repair_attempts": max(0, len(tool_runs) - 1),
                        "tool_outputs": tool_runs,
                        "reference_test_results": acceptance_runs,
                    }
                )
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "configurations": [config["name"] for config in configs],
            "results": comparisons,
        }
        if report_path:
            report_path = report_path.resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report

    @staticmethod
    def _resolve(base: Path, value: str) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (base / path).resolve()

    @staticmethod
    def _coverage(workspace: Path, value: str | None) -> float | None:
        if not value:
            return None
        path = CodingComparisonRunner._resolve(workspace, value)
        try:
            return round(float(json.loads(path.read_text(encoding="utf-8"))["totals"]["percent_covered"]), 2)
        except (OSError, ValueError, KeyError, TypeError):
            return None
