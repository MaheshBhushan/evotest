from __future__ import annotations

import json
from pathlib import Path

from autotestgen.adapters.prompting_utils import build_reference_example, compact_log_lines
from autotestgen.config import HarnessConfig
from autotestgen.models import AttemptRecord, FunctionTarget, ModuleTarget, PromptContext, ProjectInventory


class JavaScriptPromptContextBuilder:
    language_id = "javascript"
    adapter_id = "javascript"

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
        framework = self._test_framework()
        reference_examples = self._reference_examples_for(target, successful_examples or [])
        return PromptContext(
            target_id=target.target_id,
            target_kind="function" if isinstance(target, FunctionTarget) else "module",
            prompt_version=prompt_version,
            strategy=strategy,
            source_code=target.source,
            imports=[],
            nearby_context=self._nearby_context_for(target, inventory),
            existing_tests="\n\n".join(reference_examples),
            previous_failures=previous_failures,
            uncovered_lines=uncovered_lines,
            language=self.language_id,
            adapter_id=self.adapter_id,
            test_framework=framework,
            task_description=f"Generate one strong {framework} test file for the requested JavaScript/TypeScript target.",
            output_instructions=f"Output only valid {framework} test code and nothing else.",
            target_contract=self._target_contract_for(target, framework),
            execution_constraints=self._execution_constraints_for(framework),
            execution_evidence=self._execution_evidence_for(previous_attempt),
            reference_examples=reference_examples,
            output_contract=self._output_contract_for(target, framework),
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
        patterns = ("*.test.js", "*.test.ts", "*.spec.js", "*.spec.ts", "*.test.jsx", "*.test.tsx")
        for pattern in patterns:
            for test_file in sorted(self.config.project_root.rglob(pattern)):
                if "node_modules" in test_file.parts or ".autotestgen" in test_file.parts:
                    continue
                try:
                    text = test_file.read_text(encoding="utf-8")
                except OSError:
                    continue
                target_symbol = target.symbol_name if isinstance(target, FunctionTarget) else target.file_path.stem
                if target_symbol in text or target.file_path.stem in text:
                    related_blocks.append(
                        build_reference_example(
                            str(test_file.relative_to(self.config.project_root)),
                            text,
                            markers=self._markers_for(target),
                            comment_prefix="//",
                        )
                    )
        return related_blocks[:2]

    def _target_contract_for(self, target: FunctionTarget | ModuleTarget, framework: str) -> list[str]:
        contract = [
            f"Exercise the real target `{target.target_id}` directly using {framework}.",
            "Import from the target module path, not from a similarly named package or local fake object.",
        ]
        if isinstance(target, FunctionTarget):
            if target.method_kind == "instance" and target.owner_qualname:
                contract.append(
                    f"Import `{target.owner_root_name}` from the target module, construct it safely, and call `{target.symbol_name}` on that instance."
                )
            elif target.method_kind in {"class", "static"} and target.owner_qualname:
                contract.append(
                    f"Import `{target.owner_root_name}` from the target module and call `{target.owner_qualname}.{target.symbol_name}` directly."
                )
            else:
                contract.append(f"Import and call `{target.symbol_name}` from the target module.")
        else:
            contract.append("Import the module and assert on its exported behavior.")
        return contract

    @staticmethod
    def _execution_constraints_for(framework: str) -> list[str]:
        return [
            f"Use {framework} `test(...)` and `expect(...)` conventions only.",
            "Do not use network access, child_process, shell execution, time-based sleeps, or unrelated filesystem writes.",
            "Keep imports tied to the target module path. Namespace imports, alias imports, and require/import() are fine only when they resolve to the real target module.",
            "Use concrete assertions instead of `toBeDefined()` or other weak existence-only checks when a stronger behavior check is possible.",
        ]

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
    def _output_contract_for(target: FunctionTarget | ModuleTarget, framework: str) -> list[str]:
        contract = [
            f"Output only valid {framework} test code.",
            "Do not add markdown fences, prose, comments explaining the answer, or placeholder tests.",
            "Reference the requested target in every generated case.",
            "Keep multiple cases distinct and assert on concrete outputs, exceptions, or observable side effects.",
        ]
        if isinstance(target, FunctionTarget):
            contract.append("Prefer exact `expect(...).toBe(...)` or equivalent strong matchers when the expected value is inferable.")
        return contract

    @staticmethod
    def _markers_for(target: FunctionTarget | ModuleTarget) -> list[str]:
        markers = [target.file_path.stem]
        if isinstance(target, FunctionTarget):
            markers.extend([target.symbol_name, target.qualname])
            if target.owner_root_name:
                markers.append(target.owner_root_name)
        else:
            markers.extend(target.public_symbols[:2])
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

    def _test_framework(self) -> str:
        package_json = self.config.project_root / "package.json"
        if not package_json.exists():
            return "vitest"
        try:
            payload = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "vitest"
        haystack = json.dumps(payload).lower()
        if "jest" in haystack:
            return "jest"
        return "vitest"
