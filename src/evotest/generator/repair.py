from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from evotest.generator.engine import METADATA_END, METADATA_START, StaticTestGenerator
from evotest.generator.model_backed import ModelBackedTestGenerator
from evotest.models import FailureClassification, FunctionTarget, GeneratedTestArtifact, ModuleTarget


@dataclass(slots=True)
class _TestBlock:
    signature: str
    name: str
    body_lines: list[str]
    container_signature: str | None = None
    container_prelude_lines: list[str] = field(default_factory=list)


class RepairEngine:
    def __init__(
        self,
        generator: StaticTestGenerator,
        llm_generator: ModelBackedTestGenerator | None = None,
    ) -> None:
        self.generator = generator
        self.llm_generator = llm_generator

    def repair(
        self,
        *,
        target: FunctionTarget | ModuleTarget,
        previous_test_source: str,
        failure: FailureClassification,
        prompt_text: str,
        attempt_number: int,
        output_dir: Path,
        parent_attempt_number: int | None = None,
    ) -> GeneratedTestArtifact | None:
        strategy = self._repair_strategy_for(failure)
        if strategy is None:
            return None
        repaired_prompt = self._replace_prompt_strategy(prompt_text, strategy)
        llm_fallback_reason: str | None = None
        if self.llm_generator is not None:
            llm_prompt = self._build_llm_repair_prompt(
                repaired_prompt,
                previous_test_source=previous_test_source,
                failure=failure,
                target=target,
            )
            llm_artifact = self.llm_generator.generate(
                target=target,
                prompt_text=llm_prompt,
                output_dir=output_dir,
                attempt_number=attempt_number,
                generation_mode="repair",
                parent_attempt_number=parent_attempt_number,
            )
            if llm_artifact.provider_name != "heuristic":
                return llm_artifact
            llm_fallback_reason = llm_artifact.fallback_reason

        regenerated = self.generator.generate(
            target=target,
            prompt_text=repaired_prompt,
            output_dir=output_dir,
            attempt_number=attempt_number,
            generation_mode="repair",
            parent_attempt_number=parent_attempt_number,
        )
        regenerated_source = regenerated.test_source
        if failure.subtype in {"syntax_error", "indentation_error", "collection_error"}:
            return self._heuristic_artifact(
                regenerated,
                fallback_reason=llm_fallback_reason,
            )
        if failure.subtype in {"import_error", "missing_dependency", "circular_import"} and isinstance(target, FunctionTarget):
            return self._heuristic_artifact(
                regenerated,
                test_source=self._repair_imports(previous_test_source, target, regenerated_source),
                fallback_reason=llm_fallback_reason,
            )
        if failure.subtype in {
            "type_error",
            "value_error",
            "attribute_error",
            "path_error",
            "bad_fixture",
            "bad_parametrization",
            "mock_misconfiguration",
        }:
            return self._heuristic_artifact(
                regenerated,
                test_source=self._repair_invocation(previous_test_source, regenerated_source),
                fallback_reason=llm_fallback_reason,
            )
        if failure.subtype == "no_coverage_gain":
            return self._heuristic_artifact(
                regenerated,
                test_source=self._repair_coverage_expansion(previous_test_source, regenerated_source),
                fallback_reason=llm_fallback_reason,
            )
        if failure.subtype in {
            "assertion_failure",
            "equality_mismatch",
            "wrong_exception_type",
            "too_weak_oracle",
            "too_broad_assertion",
        }:
            return self._heuristic_artifact(
                regenerated,
                test_source=self._repair_assertions(previous_test_source, regenerated_source),
                fallback_reason=llm_fallback_reason,
            )
        return None

    @staticmethod
    def _heuristic_artifact(
        base_artifact: GeneratedTestArtifact,
        *,
        test_source: str | None = None,
        fallback_reason: str | None = None,
    ) -> GeneratedTestArtifact:
        if test_source is None:
            test_source = base_artifact.test_source
        return GeneratedTestArtifact(
            target_id=base_artifact.target_id,
            attempt_number=base_artifact.attempt_number,
            prompt_text=base_artifact.prompt_text,
            test_file_path=base_artifact.test_file_path,
            test_source=test_source,
            prompt_version=base_artifact.prompt_version,
            strategy=base_artifact.strategy,
            generation_mode=base_artifact.generation_mode,
            parent_attempt_number=base_artifact.parent_attempt_number,
            provider_name="heuristic",
            model_name=None,
            fallback_reason=fallback_reason,
        )

    @staticmethod
    def _repair_strategy_for(failure: FailureClassification) -> str | None:
        mapping = {
            "syntax_error": "safe_rebuild",
            "indentation_error": "safe_rebuild",
            "collection_error": "safe_rebuild",
            "import_error": "import_recovery",
            "missing_dependency": "import_recovery",
            "circular_import": "import_recovery",
            "type_error": "argument_repair",
            "value_error": "argument_repair",
            "attribute_error": "argument_repair",
            "path_error": "argument_repair",
            "bad_fixture": "argument_repair",
            "bad_parametrization": "argument_repair",
            "mock_misconfiguration": "argument_repair",
            "assertion_failure": "assertion_repair",
            "equality_mismatch": "assertion_repair",
            "wrong_exception_type": "assertion_repair",
            "too_weak_oracle": "assertion_repair",
            "too_broad_assertion": "assertion_repair",
            "no_coverage_gain": "coverage_expansion",
        }
        return mapping.get(failure.subtype)

    @staticmethod
    def _replace_prompt_strategy(prompt_text: str, strategy: str) -> str:
        match = re.search(f"{re.escape(METADATA_START)}(.*?){re.escape(METADATA_END)}", prompt_text, re.DOTALL)
        if not match:
            return prompt_text
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            payload = {}
        payload["strategy"] = strategy
        replacement = f"{METADATA_START}{json.dumps(payload)}{METADATA_END}"
        return prompt_text[: match.start()] + replacement + prompt_text[match.end() :]

    def _build_llm_repair_prompt(
        self,
        base_prompt: str,
        *,
        previous_test_source: str,
        failure: FailureClassification,
        target: FunctionTarget | ModuleTarget,
    ) -> str:
        framework = self._framework_for_target(target)
        language_label = self._language_label_for_target(target)
        repair_block = (
            "Repair task:\n"
            f"- Target: {target.target_id}\n"
            f"- Language: {language_label}\n"
            f"- Test framework: {framework}\n"
            f"- Failure category: {failure.category}\n"
            f"- Failure subtype: {failure.subtype}\n"
            f"- Failure message: {failure.message}\n"
            f"- Failure summary: {failure.summary}\n\n"
            "Current failing generated test:\n"
            f"{previous_test_source}\n\n"
            "Repair guidance:\n"
            f"{self._repair_guidance_for(failure)}\n\n"
            f"Return a full corrected {framework} test file.\n"
            "Preserve multiple test cases when present.\n"
            "Do not remove valid assertions or collapse distinct cases into one.\n"
            "Do not add markdown fences or explanations."
        )
        return f"{base_prompt}\n\n{repair_block}"

    @staticmethod
    def _framework_for_target(target: FunctionTarget | ModuleTarget) -> str:
        language = getattr(target, "language", "python")
        if language == "javascript":
            return "vitest/jest"
        if language == "go":
            return "go test"
        return "pytest"

    @staticmethod
    def _language_label_for_target(target: FunctionTarget | ModuleTarget) -> str:
        language = getattr(target, "language", "python")
        if language == "javascript":
            return "javascript"
        if language == "go":
            return "go"
        return "python"

    @staticmethod
    def _repair_guidance_for(failure: FailureClassification) -> str:
        guidance = {
            "syntax_error": "- Fix syntax only and keep the intended test behavior.",
            "indentation_error": "- Fix indentation only and keep the intended test behavior.",
            "collection_error": "- Ensure pytest can collect all generated test functions.",
            "import_error": "- Repair imports so the target can be resolved safely.",
            "missing_dependency": "- Remove or replace dependencies that are unavailable in the test environment.",
            "circular_import": "- Avoid circular imports and use deferred or fallback imports if needed.",
            "type_error": "- Repair arguments, fixtures, and mocks to match the target signature.",
            "value_error": "- Choose inputs that satisfy the target's value constraints.",
            "attribute_error": "- Fix object setup or mocks so required attributes exist.",
            "path_error": "- Use tmp_path or generated local files instead of missing paths.",
            "bad_fixture": "- Repair fixture setup and keep fixture scope minimal.",
            "bad_parametrization": "- Repair parameterization while preserving multiple meaningful cases.",
            "mock_misconfiguration": "- Repair mock behavior without overmocking unrelated behavior.",
            "assertion_failure": "- Correct the assertions to match actual target behavior.",
            "equality_mismatch": "- Replace the wrong expected values with accurate per-case expectations.",
            "wrong_exception_type": "- Assert the correct exception type for the target behavior.",
            "too_weak_oracle": "- Strengthen assertions so they verify meaningful behavior.",
            "too_broad_assertion": "- Narrow assertions so they validate the right behavior precisely.",
            "no_coverage_gain": "- Add or repair cases so they exercise uncovered branches without duplicating cases.",
        }
        return guidance.get(failure.subtype, "- Repair the test surgically while preserving intent.")

    def _repair_imports(
        self,
        previous_test_source: str,
        target: FunctionTarget,
        regenerated_source: str,
    ) -> str:
        fallback_block = self.generator.render_import_recovery_block(target)
        if "def _load_target_symbol()" in previous_test_source or "def _load_target_owner()" in previous_test_source:
            return previous_test_source
        header, blocks = RepairEngine._split_test_blocks(previous_test_source)
        if not blocks:
            return regenerated_source
        header_lines = [
            line
            for line in header.splitlines()
            if not line.startswith("from ") or line.startswith("from __future__ import ")
        ]
        repaired_header = "\n".join(line for line in header_lines if line).rstrip()
        combined_header = "\n\n".join(piece for piece in [repaired_header, fallback_block.rstrip()] if piece)
        return RepairEngine._join_test_blocks(combined_header, blocks)

    @staticmethod
    def _repair_invocation(previous_test_source: str, regenerated_source: str) -> str:
        previous_header, previous_blocks = RepairEngine._split_test_blocks(previous_test_source)
        _, regenerated_blocks = RepairEngine._split_test_blocks(regenerated_source)
        if not previous_blocks or not regenerated_blocks:
            return regenerated_source
        repaired_blocks: list[_TestBlock] = []
        for previous_block, regenerated_block in RepairEngine._aligned_blocks(previous_blocks, regenerated_blocks):
            adopted_block = RepairEngine._adopt_container(previous_block, regenerated_block)
            repaired_blocks.append(
                _TestBlock(
                    signature=adopted_block.signature,
                    name=adopted_block.name,
                    body_lines=list(adopted_block.body_lines),
                    container_signature=adopted_block.container_signature,
                    container_prelude_lines=list(adopted_block.container_prelude_lines),
                )
            )
        return RepairEngine._join_test_blocks(previous_header, repaired_blocks)

    @staticmethod
    def _repair_assertions(previous_test_source: str, regenerated_source: str) -> str:
        previous_header, previous_blocks = RepairEngine._split_test_blocks(previous_test_source)
        _, regenerated_blocks = RepairEngine._split_test_blocks(regenerated_source)
        if not previous_blocks or not regenerated_blocks:
            return regenerated_source
        repaired_blocks: list[_TestBlock] = []
        for previous_block, regenerated_block in RepairEngine._aligned_blocks(previous_blocks, regenerated_blocks):
            new_assertions = [line for line in regenerated_block.body_lines if line.strip().startswith("assert ")]
            if not new_assertions:
                repaired_blocks.append(previous_block)
                continue
            body_lines = RepairEngine._replace_assertions_in_block(previous_block.body_lines, new_assertions)
            repaired_blocks.append(
                _TestBlock(
                    signature=previous_block.signature,
                    name=previous_block.name,
                    body_lines=body_lines,
                    container_signature=previous_block.container_signature,
                    container_prelude_lines=list(previous_block.container_prelude_lines),
                )
            )
        return RepairEngine._join_test_blocks(previous_header, repaired_blocks)

    @staticmethod
    def _repair_coverage_expansion(previous_test_source: str, regenerated_source: str) -> str:
        previous_header, previous_blocks = RepairEngine._split_test_blocks(previous_test_source)
        regenerated_header, regenerated_blocks = RepairEngine._split_test_blocks(regenerated_source)
        if not previous_blocks or not regenerated_blocks:
            return regenerated_source
        merged_by_key = {RepairEngine._case_key(block.name): block for block in previous_blocks}
        ordered_keys = [RepairEngine._case_key(block.name) for block in previous_blocks]
        container_template = next((block for block in previous_blocks if block.container_signature is not None), None)
        for regenerated_block in regenerated_blocks:
            key = RepairEngine._case_key(regenerated_block.name)
            if key not in ordered_keys:
                ordered_keys.append(key)
            template_block = merged_by_key.get(key) or container_template
            merged_by_key[key] = RepairEngine._adopt_container(template_block, regenerated_block) if template_block else regenerated_block
        merged_blocks = [merged_by_key[key] for key in ordered_keys]
        return RepairEngine._join_test_blocks(regenerated_header or previous_header, merged_blocks)

    @staticmethod
    def _replace_assertions_in_block(body_lines: list[str], new_assertions: list[str]) -> list[str]:
        updated_lines: list[str] = []
        replaced = False
        for line in body_lines:
            if line.strip().startswith("assert "):
                if not replaced:
                    indent = line[: len(line) - len(line.lstrip())]
                    updated_lines.extend(f"{indent}{assertion.strip()}" for assertion in new_assertions)
                    replaced = True
                continue
            updated_lines.append(line)
        if not replaced:
            updated_lines.extend(new_assertions)
        return updated_lines

    @staticmethod
    def _split_test_blocks(source: str) -> tuple[str, list[_TestBlock]]:
        header_lines: list[str] = []
        blocks: list[_TestBlock] = []
        current_signature: str | None = None
        current_body: list[str] = []
        current_name: str | None = None
        current_container_signature: str | None = None
        current_container_prelude: list[str] = []
        active_container_signature: str | None = None
        active_container_name: str | None = None
        active_container_prelude: list[str] = []
        active_container_has_tests = False

        def flush_container() -> None:
            nonlocal active_container_signature, active_container_name, active_container_prelude, active_container_has_tests
            if active_container_signature is not None and not active_container_has_tests:
                header_lines.extend([active_container_signature, *active_container_prelude])
            active_container_signature = None
            active_container_name = None
            active_container_prelude = []
            active_container_has_tests = False

        def flush_block() -> None:
            nonlocal current_signature, current_body, current_name, current_container_signature, current_container_prelude
            if current_signature is None or current_name is None:
                return
            blocks.append(
                _TestBlock(
                    signature=current_signature,
                    name=current_name,
                    body_lines=current_body,
                    container_signature=current_container_signature,
                    container_prelude_lines=list(current_container_prelude),
                )
            )
            current_signature = None
            current_body = []
            current_name = None
            current_container_signature = None
            current_container_prelude = []

        for line in source.splitlines():
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            is_test_class = indent == 0 and stripped.startswith("class Test")
            is_top_level_test = indent == 0 and stripped.startswith("def test_")
            is_class_test = active_container_signature is not None and indent == 4 and stripped.startswith("def test_")
            if is_test_class:
                flush_block()
                flush_container()
                active_container_signature = line
                active_container_name = RepairEngine._class_name_from_signature(stripped)
                active_container_prelude = []
                active_container_has_tests = False
                continue
            if is_top_level_test or is_class_test:
                flush_block()
                if is_top_level_test:
                    flush_container()
                    current_signature = line
                    current_name = RepairEngine._test_name_from_signature(stripped)
                else:
                    current_signature = line
                    method_name = RepairEngine._test_name_from_signature(stripped)
                    current_name = f"{active_container_name}.{method_name}" if active_container_name else method_name
                    current_container_signature = active_container_signature
                    current_container_prelude = list(active_container_prelude)
                    active_container_prelude = []
                    active_container_has_tests = True
                current_body = []
                continue
            if current_signature is None:
                if active_container_signature is not None:
                    if line and not line.startswith(" "):
                        flush_container()
                        header_lines.append(line)
                    else:
                        active_container_prelude.append(line)
                else:
                    header_lines.append(line)
            else:
                current_body.append(line)
        flush_block()
        flush_container()
        return "\n".join(header_lines).strip(), blocks

    @staticmethod
    def _join_test_blocks(header: str, blocks: list[_TestBlock]) -> str:
        pieces: list[str] = [piece for piece in [header] if piece]
        index = 0
        while index < len(blocks):
            block = blocks[index]
            if block.container_signature is None:
                pieces.append(RepairEngine._render_block(block))
                index += 1
                continue
            class_lines = [block.container_signature, *block.container_prelude_lines]
            while index < len(blocks) and blocks[index].container_signature == block.container_signature:
                class_lines.append(RepairEngine._render_block(blocks[index]))
                index += 1
            pieces.append("\n".join(class_lines).rstrip())
        return "\n\n".join(pieces).rstrip() + "\n"

    @staticmethod
    def _aligned_blocks(
        previous_blocks: list[_TestBlock],
        regenerated_blocks: list[_TestBlock],
    ) -> list[tuple[_TestBlock, _TestBlock]]:
        regenerated_by_key = {RepairEngine._case_key(block.name): block for block in regenerated_blocks}
        pairs: list[tuple[_TestBlock, _TestBlock]] = []
        for index, previous_block in enumerate(previous_blocks):
            key = RepairEngine._case_key(previous_block.name)
            regenerated_block = regenerated_by_key.get(key)
            if regenerated_block is None:
                regenerated_block = regenerated_blocks[min(index, len(regenerated_blocks) - 1)]
            pairs.append((previous_block, regenerated_block))
        return pairs

    @staticmethod
    def _case_key(test_name: str) -> str:
        match = re.search(r"_case_(\d+)$", test_name)
        return match.group(1) if match else test_name

    @staticmethod
    def _render_block(block: _TestBlock) -> str:
        return "\n".join([block.signature, *block.body_lines]).rstrip()

    @staticmethod
    def _adopt_container(previous_block: _TestBlock | None, regenerated_block: _TestBlock) -> _TestBlock:
        if previous_block is None or previous_block.container_signature is None or regenerated_block.container_signature is not None:
            return regenerated_block
        signature_indent = previous_block.signature[: len(previous_block.signature) - len(previous_block.signature.lstrip())]
        adjusted_signature = RepairEngine._ensure_bound_method_signature(regenerated_block.signature.lstrip())
        adjusted_signature = signature_indent + adjusted_signature
        adjusted_body = [f"{signature_indent}{line}" if line else line for line in regenerated_block.body_lines]
        qualified_name = regenerated_block.name
        if "." not in qualified_name and "." in previous_block.name:
            qualified_name = f"{previous_block.name.split('.', 1)[0]}.{qualified_name}"
        return _TestBlock(
            signature=adjusted_signature,
            name=qualified_name,
            body_lines=adjusted_body,
            container_signature=previous_block.container_signature,
            container_prelude_lines=list(previous_block.container_prelude_lines),
        )

    @staticmethod
    def _ensure_bound_method_signature(signature: str) -> str:
        match = re.match(
            r"^(?P<prefix>(?:async\s+def|def)\s+[^(]+\()(?P<params>[^)]*)(?P<suffix>\)\s*(?:->\s*[^:]+)?\s*:)$",
            signature.strip(),
        )
        if match is None:
            return signature
        params = match.group("params").strip()
        if not params:
            params = "self"
        elif not params.startswith("self"):
            params = f"self, {params}"
        return f"{match.group('prefix')}{params}{match.group('suffix')}"

    @staticmethod
    def _class_name_from_signature(signature: str) -> str:
        return signature.removeprefix("class ").split("(", 1)[0].split(":", 1)[0]

    @staticmethod
    def _test_name_from_signature(signature: str) -> str:
        return signature.removeprefix("def ").split("(", 1)[0]
