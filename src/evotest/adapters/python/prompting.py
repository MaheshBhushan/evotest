from __future__ import annotations

from pathlib import Path

from evotest.adapters.prompting_utils import build_reference_example, compact_log_lines
from evotest.config import HarnessConfig
from evotest.models import AttemptRecord, FunctionTarget, ModuleTarget, PromptContext, ProjectInventory


class PythonPromptContextBuilder:
    language_id = "python"
    adapter_id = "python"
    test_framework = "pytest"

    def __init__(self, config: HarnessConfig) -> None:
        self.config = config

    def build(
        self,
        *,
        target: FunctionTarget | ModuleTarget,
        inventory: ProjectInventory,
        previous_failures: list[str],
        uncovered_lines: list[int],
        strategy: str,
        prompt_version: str,
        previous_attempt: AttemptRecord | None = None,
        successful_examples: list[tuple[str, str]] | None = None,
    ) -> PromptContext:
        imports = [
            f"{import_ref.level}:{import_ref.module}:{', '.join(import_ref.names)}"
            for import_ref in target.imports
        ]
        reference_examples = self._reference_examples_for(target, successful_examples or [])
        return PromptContext(
            target_id=target.target_id,
            target_kind="function" if isinstance(target, FunctionTarget) else "module",
            prompt_version=prompt_version,
            strategy=strategy,
            source_code=target.source,
            imports=imports,
            nearby_context=self._nearby_context_for(target, inventory),
            existing_tests="\n\n".join(reference_examples),
            previous_failures=previous_failures,
            uncovered_lines=uncovered_lines,
            language=self.language_id,
            adapter_id=self.adapter_id,
            test_framework=self.test_framework,
            task_description="Generate one strong pytest test file for the requested Python target.",
            output_instructions="Output only valid pytest code and nothing else.",
            target_contract=self._target_contract_for(target),
            execution_constraints=self._execution_constraints_for(target),
            execution_evidence=self._execution_evidence_for(previous_attempt),
            reference_examples=reference_examples,
            output_contract=self._output_contract_for(target),
        )

    def _reference_examples_for(
        self,
        target: FunctionTarget | ModuleTarget,
        successful_examples: list[tuple[str, str]],
    ) -> list[str]:
        examples: list[str] = []
        for target_id, test_source in successful_examples[:1]:
            example = build_reference_example(
                f"prior success from {target_id}",
                test_source,
                markers=self._markers_for(target),
                comment_prefix="#",
            )
            if example:
                examples.append(example)
        examples.extend(self._existing_tests_for(target))
        return examples[:2]

    def _existing_tests_for(self, target: FunctionTarget | ModuleTarget) -> list[str]:
        tests_root = self.config.project_root / "tests"
        if not tests_root.exists():
            return []
        related_blocks: list[str] = []
        for test_file in sorted(tests_root.rglob("test_*.py")):
            try:
                text = test_file.read_text(encoding="utf-8")
            except OSError:
                continue
            if target.module_name in text or target.file_path.stem in test_file.stem:
                related_blocks.append(
                    build_reference_example(
                        str(test_file.relative_to(self.config.project_root)),
                        text,
                        markers=self._markers_for(target),
                        comment_prefix="#",
                    )
                )
        return related_blocks[:2]

    def _target_contract_for(self, target: FunctionTarget | ModuleTarget) -> list[str]:
        contract = [
            f"Exercise the real target `{target.target_id}` directly.",
            f"Use pytest and keep the test file focused on this {('function' if isinstance(target, FunctionTarget) else 'module')}.",
        ]
        if isinstance(target, FunctionTarget):
            if target.method_kind == "instance" and target.owner_qualname:
                contract.append(
                    f"Import `{target.owner_root_name}` from `{target.module_name}`, construct it safely, and call `{target.symbol_name}` on the instance."
                )
            elif target.method_kind in {"class", "static"} and target.owner_qualname:
                contract.append(
                    f"Import `{target.owner_root_name}` from `{target.module_name}` and call `{target.owner_qualname}.{target.symbol_name}` directly."
                )
            else:
                contract.append(f"Import `{target.symbol_name}` from `{target.module_name}` and call it directly.")
        else:
            contract.append(f"Import module `{target.module_name}` and assert on the module's exposed behavior.")
        return contract

    @staticmethod
    def _execution_constraints_for(target: FunctionTarget | ModuleTarget) -> list[str]:
        constraints = [
            "Do not add markdown fences, prose, TODOs, placeholder asserts, or skipped tests.",
            "Keep side effects local: no network calls, subprocesses, sleeps, environment mutation, or writes outside temporary paths.",
            "Prefer direct imports. Use importlib fallback logic only when the strategy or failures show import resolution problems.",
            "Every test must assert a concrete value, raised exception, or observable side effect. Avoid assertion-only smoke tests when a stronger oracle is possible.",
        ]
        if isinstance(target, FunctionTarget) and ("path" in target.symbol_name.lower() or target.io_hint):
            constraints.append("If the target needs filesystem input, use tmp_path or generated local files.")
        return constraints

    def _execution_evidence_for(self, previous_attempt: AttemptRecord | None) -> list[str]:
        if previous_attempt is None:
            return ["No previous attempt exists. Start with the simplest strong case that directly exercises the target."]
        evidence = [
            f"Previous attempt number: {previous_attempt.attempt_number}",
            f"Previous generated test path: {previous_attempt.generated_test_path}",
            f"Previous generation mode: {previous_attempt.generation_mode}",
        ]
        if previous_attempt.failure is not None:
            evidence.append(
                f"Previous failure: {previous_attempt.failure.subtype} -> {previous_attempt.failure.message}"
            )
        if previous_attempt.execution_result is not None:
            result = previous_attempt.execution_result
            evidence.append(f"Previous exit code: {result.exit_code}")
            if result.coverage is not None:
                evidence.append(
                    f"Previous coverage: {result.coverage.coverage_percent:.2f}% with missing lines {result.coverage.missing_lines or 'none'}"
                )
            evidence.extend(compact_log_lines(result.stderr, prefix="stderr"))
            evidence.extend(compact_log_lines(result.stdout, prefix="stdout", max_lines=2))
        return evidence

    @staticmethod
    def _output_contract_for(target: FunctionTarget | ModuleTarget) -> list[str]:
        contract = [
            "Output only valid pytest code.",
            "Define collectable `test_...` functions or `class Test...` methods only.",
            "Reference the requested target in every generated case.",
            "Keep multiple cases distinct; do not collapse them into one broad assertion.",
        ]
        if isinstance(target, FunctionTarget):
            contract.append("Use concrete expected values whenever the target behavior is inferable from the source.")
        return contract

    @staticmethod
    def _markers_for(target: FunctionTarget | ModuleTarget) -> list[str]:
        markers = [target.file_path.stem]
        if isinstance(target, FunctionTarget):
            markers.extend([target.symbol_name, target.qualname, target.module_name])
            if target.owner_root_name:
                markers.append(target.owner_root_name)
        else:
            markers.extend(target.public_symbols[:2])
            markers.append(target.module_name)
        return [marker for marker in markers if marker]

    @staticmethod
    def _nearby_context_for(
        target: FunctionTarget | ModuleTarget,
        inventory: ProjectInventory,
    ) -> str:
        if isinstance(target, ModuleTarget):
            return "\n".join(
                f"{function.qualname} score={function.target_score}"
                for function in inventory.functions
                if function.file_path == target.file_path
            )
        related = [
            f"{function.qualname} score={function.target_score}"
            for function in inventory.functions
            if function.file_path == target.file_path and function.target_id != target.target_id
        ]
        return "\n".join(related[:10])
