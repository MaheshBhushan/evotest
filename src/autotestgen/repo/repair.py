from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from autotestgen.adapters.base import LanguageAdapter
from autotestgen.config import HarnessConfig
from autotestgen.generator.provider import LLMProvider, OpenAIResponsesProvider, ProviderResponseError
from autotestgen.models import AttemptRecord, ExecutionResult, FailureClassification, FunctionTarget, ModuleTarget

Target = FunctionTarget | ModuleTarget


@dataclass(slots=True)
class SourceRepairOutcome:
    attempted: bool
    applied: bool
    verified: bool
    source_file: str | None = None
    provider_name: str | None = None
    model_name: str | None = None
    error: str | None = None
    validation_result: ExecutionResult | None = None


class RepoRepairEngine:
    def __init__(self, config: HarnessConfig, *, provider: LLMProvider | None = None) -> None:
        self.config = config.resolved()
        self.provider = provider

    def attempt(
        self,
        *,
        target: Target,
        previous_attempt: AttemptRecord,
        adapter: LanguageAdapter,
        repair_number: int,
    ) -> SourceRepairOutcome:
        provider = self._provider()
        if provider is None:
            return SourceRepairOutcome(
                attempted=False,
                applied=False,
                verified=False,
                error="OPENAI_API_KEY is not set.",
            )
        source_path = target.file_path
        try:
            original_source = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            return SourceRepairOutcome(
                attempted=True,
                applied=False,
                verified=False,
                source_file=source_path.relative_to(self.config.project_root).as_posix(),
                provider_name=provider.name,
                model_name=self.config.model_name,
                error=f"Could not read target source file: {exc}",
            )
        prompt_text = self._build_prompt(
            target=target,
            current_source=original_source,
            previous_attempt=previous_attempt,
            repair_number=repair_number,
        )
        try:
            rewritten_source = provider.generate_text(
                prompt_text,
                model=self.config.model_name,
                temperature=self.config.temperature,
                max_output_tokens=self.config.max_output_tokens,
                instructions=self._source_instructions_for(target),
            )
        except ProviderResponseError as exc:
            return SourceRepairOutcome(
                attempted=True,
                applied=False,
                verified=False,
                source_file=source_path.relative_to(self.config.project_root).as_posix(),
                provider_name=provider.name,
                model_name=self.config.model_name,
                error=str(exc),
            )
        normalized_source = self._normalize_output(rewritten_source)
        if not normalized_source.strip():
            return SourceRepairOutcome(
                attempted=True,
                applied=False,
                verified=False,
                source_file=source_path.relative_to(self.config.project_root).as_posix(),
                provider_name=provider.name,
                model_name=self.config.model_name,
                error="Model returned empty source output.",
            )
        if normalized_source.strip() == original_source.strip():
            return SourceRepairOutcome(
                attempted=True,
                applied=False,
                verified=False,
                source_file=source_path.relative_to(self.config.project_root).as_posix(),
                provider_name=provider.name,
                model_name=self.config.model_name,
                error="Model returned the original source unchanged.",
            )

        source_path.write_text(normalized_source, encoding="utf-8")
        validation_result = adapter.run(
            target=target,
            test_source=previous_attempt.generated_test_source,
            test_file_path=previous_attempt.generated_test_path,
            attempt_number=10000 + repair_number,
        )
        if not validation_result.success:
            source_path.write_text(original_source, encoding="utf-8")
            return SourceRepairOutcome(
                attempted=True,
                applied=False,
                verified=False,
                source_file=source_path.relative_to(self.config.project_root).as_posix(),
                provider_name=provider.name,
                model_name=self.config.model_name,
                error=self._failure_message(validation_result),
                validation_result=validation_result,
            )
        return SourceRepairOutcome(
            attempted=True,
            applied=True,
            verified=True,
            source_file=source_path.relative_to(self.config.project_root).as_posix(),
            provider_name=provider.name,
            model_name=self.config.model_name,
            validation_result=validation_result,
        )

    def _provider(self) -> LLMProvider | None:
        if self.provider is not None:
            return self.provider
        if not self.config.openai_api_key:
            return None
        self.provider = OpenAIResponsesProvider(api_key=self.config.openai_api_key)
        return self.provider

    def _build_prompt(
        self,
        *,
        target: Target,
        current_source: str,
        previous_attempt: AttemptRecord,
        repair_number: int,
    ) -> str:
        execution_result = previous_attempt.execution_result
        failure = previous_attempt.failure or self._fallback_failure(execution_result)
        return (
            "Repo repair task:\n"
            f"- Repair number: {repair_number}\n"
            f"- Target: {target.target_id}\n"
            f"- Language: {getattr(target, 'language', 'python')}\n"
            f"- Source file: {target.file_path.relative_to(self.config.project_root).as_posix()}\n"
            f"- Failure category: {failure.category}\n"
            f"- Failure subtype: {failure.subtype}\n"
            f"- Failure message: {failure.message}\n"
            f"- Failure summary: {failure.summary}\n\n"
            "Goal:\n"
            "- Fix the target repository source file so the failing generated test passes.\n"
            "- Repair the real implementation, not the test.\n"
            "- Make the smallest safe change that addresses the failure.\n"
            "- Preserve unrelated behavior and public APIs.\n\n"
            "Current source file:\n"
            f"{current_source}\n\n"
            "Failing generated test:\n"
            f"{previous_attempt.generated_test_source}\n\n"
            "Execution stdout:\n"
            f"{self._compact_log(execution_result.stdout if execution_result else '')}\n\n"
            "Execution stderr:\n"
            f"{self._compact_log(execution_result.stderr if execution_result else '')}\n\n"
            "Return the full updated source file only. Do not return a diff, markdown fences, or explanations."
        )

    @staticmethod
    def _source_instructions_for(target: Target) -> str:
        language = getattr(target, "language", "python")
        if language == "javascript":
            return (
                "Return only valid JavaScript or TypeScript source code for the full updated source file. "
                "Do not include prose, markdown fences, or explanations."
            )
        if language == "go":
            return "Return only valid Go source code for the full updated source file. Do not include prose, markdown fences, or explanations."
        return "Return only valid Python source code for the full updated source file. Do not include prose, markdown fences, or explanations."

    @staticmethod
    def _normalize_output(raw_source: str) -> str:
        text = raw_source.strip()
        fence_match = re.fullmatch(r"```(?:[A-Za-z0-9_+-]+)?\s*(.*?)```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()
        return text.rstrip() + "\n"

    @staticmethod
    def _compact_log(text: str, *, max_chars: int = 4000) -> str:
        cleaned = text.strip()
        if not cleaned:
            return "none"
        if len(cleaned) <= max_chars:
            return cleaned
        return cleaned[-max_chars:]

    @staticmethod
    def _failure_message(result: ExecutionResult) -> str:
        combined = "\n".join(part for part in [result.stdout, result.stderr] if part).strip()
        if not combined:
            return "Generated validation test still failed after repo repair."
        lines = [line.strip() for line in combined.splitlines() if line.strip()]
        return lines[-1][:240] if lines else "Generated validation test still failed after repo repair."

    @staticmethod
    def _fallback_failure(result: ExecutionResult | None) -> FailureClassification:
        return FailureClassification(
            category="runtime",
            subtype="runtime_error",
            recoverable=True,
            message=RepoRepairEngine._failure_message(result) if result is not None else "Previous validation failed.",
            summary="Execution failed and requires a repository source repair.",
        )
