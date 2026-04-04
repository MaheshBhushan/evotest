from __future__ import annotations

import json
import re
from pathlib import Path

from evotest.adapters.javascript.generation import JavaScriptHeuristicGenerator
from evotest.generator.engine import METADATA_END, METADATA_START
from evotest.models import FailureClassification, FunctionTarget, GeneratedTestArtifact, ModuleTarget


class JavaScriptRepairEngine:
    language_id = "javascript"
    adapter_id = "javascript"

    def __init__(self, project_root: Path) -> None:
        self.generator = JavaScriptHeuristicGenerator(project_root)

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
        llm_generator=None,
    ) -> GeneratedTestArtifact | None:
        if not failure.recoverable:
            return None
        strategy = self._repair_strategy_for(failure)
        repaired_prompt = self._replace_prompt_strategy(prompt_text, strategy) if strategy else prompt_text
        if llm_generator is not None:
            llm_prompt = self._build_llm_repair_prompt(
                repaired_prompt,
                previous_test_source=previous_test_source,
                failure=failure,
                target=target,
            )
            llm_artifact = llm_generator.generate(
                target=target,
                prompt_text=llm_prompt,
                output_dir=output_dir,
                attempt_number=attempt_number,
                generation_mode="repair",
                parent_attempt_number=parent_attempt_number,
            )
            if llm_artifact.provider_name != "heuristic":
                llm_artifact.language = self.language_id
                llm_artifact.adapter_id = self.adapter_id
                return llm_artifact
        artifact = self.generator.generate(
            target=target,
            prompt_text=repaired_prompt,
            output_dir=output_dir,
            attempt_number=attempt_number,
            generation_mode="repair",
            parent_attempt_number=parent_attempt_number,
        )
        original_source = artifact.test_source
        rewritten_source = self._rewrite_heuristic_repair(previous_test_source, original_source, failure)
        artifact.test_source = rewritten_source
        if rewritten_source == original_source:
            artifact.generation_mode = "regenerate"
        artifact.language = self.language_id
        artifact.adapter_id = self.adapter_id
        return artifact

    @staticmethod
    def _repair_strategy_for(failure: FailureClassification) -> str | None:
        mapping = {
            "syntax_error": "safe_rebuild",
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
        repair_block = (
            "Repair task:\n"
            f"- Target: {target.target_id}\n"
            "- Language: javascript\n"
            "- Test framework: vitest/jest\n"
            f"- Failure category: {failure.category}\n"
            f"- Failure subtype: {failure.subtype}\n"
            f"- Failure message: {failure.message}\n"
            f"- Failure summary: {failure.summary}\n\n"
            "Current failing generated test:\n"
            f"{previous_test_source}\n\n"
            "Repair guidance:\n"
            f"{self._repair_guidance_for(failure)}\n\n"
            "Return a full corrected JavaScript/TypeScript vitest/jest test file.\n"
            "Preserve multiple cases when present.\n"
            "Do not add markdown fences or explanations."
        )
        return f"{base_prompt}\n\n{repair_block}"

    @staticmethod
    def _repair_guidance_for(failure: FailureClassification) -> str:
        guidance = {
            "import_error": "- Repair imports so they resolve from the real target module path.",
            "missing_dependency": "- Remove or replace missing runtime dependencies.",
            "type_error": "- Repair arguments or object setup to match the target call shape.",
            "attribute_error": "- Fix the owner object or namespace binding before calling the target.",
            "assertion_failure": "- Correct the expectation while keeping the target invocation intact.",
            "no_coverage_gain": "- Add a new case that hits uncovered behavior instead of repeating the same path.",
        }
        return guidance.get(failure.subtype, "- Repair the failing test surgically while preserving target coverage intent.")

    def _rewrite_heuristic_repair(
        self,
        previous_test_source: str,
        regenerated_source: str,
        failure: FailureClassification,
    ) -> str:
        if failure.subtype == "no_coverage_gain":
            merged = self._append_missing_test_blocks(previous_test_source, regenerated_source)
            if merged:
                return merged
        if failure.subtype in {
            "assertion_failure",
            "equality_mismatch",
            "wrong_exception_type",
            "too_weak_oracle",
            "too_broad_assertion",
        }:
            replaced = self._replace_expect_lines(previous_test_source, regenerated_source)
            if replaced:
                return replaced
        return regenerated_source

    @staticmethod
    def _append_missing_test_blocks(previous_test_source: str, regenerated_source: str) -> str | None:
        block_pattern = re.compile(r"(?ms)^(?:test|it)\s*\(.*?\);\s*$")
        previous_blocks = block_pattern.findall(previous_test_source)
        regenerated_blocks = block_pattern.findall(regenerated_source)
        if not previous_blocks or not regenerated_blocks:
            return None
        merged = previous_test_source.rstrip()
        existing = {block.strip() for block in previous_blocks}
        for block in regenerated_blocks:
            if block.strip() in existing:
                continue
            merged = f"{merged}\n\n{block.strip()}"
        return f"{merged}\n"

    @staticmethod
    def _replace_expect_lines(previous_test_source: str, regenerated_source: str) -> str | None:
        previous_lines = previous_test_source.splitlines()
        regenerated_asserts = [line.strip() for line in regenerated_source.splitlines() if "expect(" in line]
        if not regenerated_asserts:
            return None
        replaced_lines: list[str] = []
        replacement_index = 0
        for line in previous_lines:
            if "expect(" in line and replacement_index < len(regenerated_asserts):
                indent = line[: len(line) - len(line.lstrip())]
                replaced_lines.append(f"{indent}{regenerated_asserts[replacement_index]}")
                replacement_index += 1
            else:
                replaced_lines.append(line)
        return "\n".join(replaced_lines).rstrip() + "\n"
