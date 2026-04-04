from __future__ import annotations

from autotestgen.adapters.prompting_utils import build_reference_example, compact_log_lines
from autotestgen.config import HarnessConfig
from autotestgen.models import AttemptRecord, FunctionTarget, ModuleTarget, PromptContext, ProjectInventory


class GoPromptContextBuilder:
    language_id = "go"
    adapter_id = "go"
    test_framework = "go test"

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
            f"{import_ref.module}:{', '.join(import_ref.names)}"
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
            task_description="Generate one strong Go `_test.go` file for the requested target.",
            output_instructions="Output only valid Go `_test.go` code and nothing else.",
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
                comment_prefix="//",
            )
            if example:
                examples.append(example)
        examples.extend(self._existing_tests_for(target))
        return examples[:2]

    def _existing_tests_for(self, target: FunctionTarget | ModuleTarget) -> list[str]:
        related_blocks: list[str] = []
        for test_file in sorted(self.config.project_root.rglob("*_test.go")):
            if "vendor" in test_file.parts or ".autotestgen" in test_file.parts:
                continue
            try:
                text = test_file.read_text(encoding="utf-8")
            except OSError:
                continue
            target_symbol = target.symbol_name if isinstance(target, FunctionTarget) else target.file_path.stem
            if target_symbol in text or target.file_path.stem in test_file.stem:
                related_blocks.append(
                    build_reference_example(
                        str(test_file.relative_to(self.config.project_root)),
                        text,
                        markers=self._markers_for(target),
                        comment_prefix="//",
                    )
                )
        return related_blocks[:2]

    def _target_contract_for(self, target: FunctionTarget | ModuleTarget) -> list[str]:
        contract = [
            f"Exercise the real target `{target.target_id}` directly using `go test`.",
            "Import the real target package and call the requested exported symbol, not a redefined local helper.",
        ]
        if isinstance(target, FunctionTarget):
            if target.method_kind == "instance" and target.owner_qualname:
                contract.append(
                    f"Construct `{target.owner_root_name}` with valid inputs and call `{target.symbol_name}` on the instance."
                )
            elif target.method_kind in {"class", "static"} and target.owner_qualname:
                contract.append(f"Call `{target.owner_qualname}.{target.symbol_name}` through the imported package alias.")
            else:
                contract.append(f"Call `{target.symbol_name}` through the imported target package alias.")
        else:
            contract.append("Assert on exported module/package behavior that can be checked from a generated `_test.go` file.")
        return contract

    @staticmethod
    def _execution_constraints_for(target: FunctionTarget | ModuleTarget) -> list[str]:
        constraints = [
            "Emit runnable `_test.go` code with the `testing` package only. Do not emit prose, markdown fences, or example code outside the test file.",
            "Avoid network access, shell execution, sleeps, or writes outside a temporary test workspace.",
            "Use strong comparisons (`if got != want`, `errors.Is`, or observable side effects) instead of weak nil-only checks when a stronger oracle is available.",
        ]
        if isinstance(target, FunctionTarget) and target.io_hint:
            constraints.append("If the target needs files or paths, create them within the test workspace.")
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
            "Output only valid Go `_test.go` code.",
            "Reference the requested target in every generated case.",
            "Keep cases distinct and assert on concrete outputs, errors, or side effects.",
        ]
        if isinstance(target, FunctionTarget):
            contract.append("Prefer exact expected values when the target behavior is inferable from the source.")
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
