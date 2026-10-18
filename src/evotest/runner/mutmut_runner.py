from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from evotest.config import HarnessConfig
from evotest.models import FunctionTarget, ModuleTarget, MutationResult


class MutmutRunner:
    """Evaluate only the generated suite, inside the disposable pytest workspace."""

    def __init__(self, config: HarnessConfig, python_executable: str) -> None:
        self.config = config
        self.python_executable = python_executable

    def run(
        self,
        project_copy: Path,
        target: FunctionTarget | ModuleTarget,
        test_path: Path,
        env: dict[str, str],
    ) -> MutationResult:
        evidence = self.config.artifacts_dir / "mutations" / uuid4().hex
        evidence.mkdir(parents=True)
        result = MutationResult(status="error", evidence_dir=str(evidence))
        relative_source = target.file_path.relative_to(self.config.project_root)
        snapshot = evidence / "source" / relative_source
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(project_copy / relative_source, snapshot)
        shutil.copy2(test_path, evidence / "generated_test.py")
        deadline = time.monotonic() + self.config.mutation_timeout_seconds
        try:
            if not hasattr(os, "fork"):
                raise RuntimeError("mutmut 3 requires fork support; use Linux, macOS, or WSL.")
            self._configure(project_copy, relative_source, test_path)
            self._command(["run", "--max-children", "1"], project_copy, env, deadline, evidence)
            output = self._command(["results", "--all", "true"], project_copy, env, deadline, evidence)
            mutants = self._parse_results(output)
            counts: dict[str, int] = {}
            for mutant in mutants:
                status = mutant["status"]
                counts[status] = counts.get(status, 0) + 1
            result.counts = counts
            # Save IDs and outcomes before exporting diffs, which may exhaust the budget.
            counterexamples_path = evidence / "counterexamples.json"
            counterexamples_path.write_text(json.dumps(mutants, indent=2), encoding="utf-8")
            for mutant in mutants:
                mutant["diff"] = self._command(
                    ["show", mutant["id"]], project_copy, env, deadline, evidence
                )
                counterexamples_path.write_text(json.dumps(mutants, indent=2), encoding="utf-8")
            if counts.get("not checked") or counts.get("check was interrupted by user"):
                raise RuntimeError("mutmut left unevaluated mutants.")
            total = sum(counts.values()) - counts.get("skipped", 0)
            result.status = "completed"
            result.score = round(100.0 * counts.get("killed", 0) / total, 2) if total else None
        except subprocess.TimeoutExpired:
            result.status = "timeout"
            result.error = "Mutation testing exceeded its time budget."
        except (OSError, RuntimeError, ValueError) as exc:
            result.error = str(exc)
        finally:
            # Keep raw mutant source and metadata even if evaluation or diff export failed.
            mutated_source = project_copy / "mutants" / relative_source
            for source in (mutated_source, Path(str(mutated_source) + ".meta")):
                if source.exists():
                    destination = evidence / "mutants" / source.relative_to(project_copy / "mutants")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            (evidence / "result.json").write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
        return result

    @staticmethod
    def _configure(project: Path, source: Path, test_path: Path) -> None:
        # Replace config only in the disposable copy, so project mutmut hooks and
        # pytest addopts cannot change which suite or source file gets measured.
        source_paths = [
            entry.name for entry in sorted(project.iterdir())
            if not entry.name.startswith(".")
            and entry.name not in {"mutants", "venv", "env", "node_modules", "tests", "test"}
            and (entry.is_dir() or entry.suffix == ".py")
        ]
        settings = {
            "source_paths": source_paths,
            "only_mutate": [source.as_posix()],
            "also_copy": [test_path.parent.relative_to(project).as_posix(), "evotest_mutation_pytest.ini"],
            "pytest_add_cli_args_test_selection": [test_path.relative_to(project).as_posix()],
            "pytest_add_cli_args": ["-c", "evotest_mutation_pytest.ini", "-p", "no:cacheprovider"],
        }
        (project / "pyproject.toml").write_text(
            "[tool.mutmut]\n" + "".join(f"{key} = {json.dumps(value)}\n" for key, value in settings.items()),
            encoding="utf-8",
        )
        (project / "evotest_mutation_pytest.ini").write_text("[pytest]\n", encoding="utf-8")

    @staticmethod
    def _parse_results(output: str) -> list[dict[str, str]]:
        mutants = []
        statuses = {
            "killed", "survived", "no tests", "timeout", "suspicious", "skipped",
            "caught by type check", "check was interrupted by user", "not checked", "segfault",
        }
        for line in output.splitlines():
            if not line.strip():
                continue
            match = re.fullmatch(r"\s*(\S+): (.+)", line)
            if match is None or match[2] not in statuses:
                raise ValueError(f"Unrecognized mutmut result: {line}")
            mutants.append({"id": match[1], "status": match[2]})
        return mutants

    def _command(
        self, args: list[str], project: Path, env: dict[str, str], deadline: float, evidence: Path,
    ) -> str:
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise subprocess.TimeoutExpired(args, self.config.mutation_timeout_seconds)
        command = [self.python_executable, "-u", "-m", "mutmut", *args]
        with (evidence / "mutmut.log").open("a", encoding="utf-8") as log:
            log.write(f"Running mutmut {' '.join(args)}\n")
            log.flush()
            with subprocess.Popen(
                command, cwd=project, env=env, stdout=log, stderr=subprocess.STDOUT,
                text=True, start_new_session=True,
            ) as process:
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    raise
            if process.returncode:
                raise RuntimeError(f"mutmut {' '.join(args)} exited {process.returncode}; see {evidence / 'mutmut.log'}")
        # Read just this command's output for parsing; the complete log remains on disk.
        return (evidence / "mutmut.log").read_text(encoding="utf-8").rsplit(
            f"Running mutmut {' '.join(args)}\n", 1
        )[1]
