from __future__ import annotations

import json
import re
from pathlib import Path

from autotestgen.adapters.base import LanguageAdapter
from autotestgen.adapters.registry import get_adapter, get_adapter_for_language
from autotestgen.config import HarnessConfig
from autotestgen.generator.engine import METADATA_END, METADATA_START, PROMPT_VERSION
from autotestgen.generator.provider import (
    LLMProvider,
    OpenAIResponsesProvider,
    ProviderConfigurationError,
    ProviderResponseError,
)
from autotestgen.models import FunctionTarget, GeneratedTestArtifact, ModuleTarget


class ModelBackedTestGenerator:
    def __init__(
        self,
        *,
        config: HarnessConfig,
        adapter: LanguageAdapter | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        self.config = config.resolved()
        self.adapter = adapter or get_adapter(self.config)
        self.provider = provider

    def build_prompt_metadata(self, prompt_text: str) -> dict[str, object]:
        match = re.search(f"{re.escape(METADATA_START)}(.*?){re.escape(METADATA_END)}", prompt_text, re.DOTALL)
        if match is None:
            return {
                "prompt_version": PROMPT_VERSION,
                "strategy": "baseline",
                "previous_failures": [],
                "uncovered_lines": [],
            }
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            payload = {}
        return {
            "prompt_version": str(payload.get("prompt_version", PROMPT_VERSION)),
            "strategy": str(payload.get("strategy", "baseline")),
            "previous_failures": [str(item) for item in payload.get("previous_failures", [])],
            "uncovered_lines": [int(item) for item in payload.get("uncovered_lines", [])],
        }

    def generate(
        self,
        target: FunctionTarget | ModuleTarget,
        prompt_text: str,
        output_dir: Path,
        attempt_number: int,
        *,
        generation_mode: str = "regenerate",
        parent_attempt_number: int | None = None,
        adapter: LanguageAdapter | None = None,
    ) -> GeneratedTestArtifact:
        active_adapter = adapter or self._adapter_for_target(target)
        fallback_reason: str | None = None
        if self._should_use_llm():
            provider = self._provider()
            if provider is None:
                fallback_reason = self._provider_unavailable_reason()
            else:
                try:
                    raw_source = provider.generate_text(
                        prompt_text,
                        model=self.config.model_name,
                        temperature=self.config.temperature,
                        max_output_tokens=self.config.max_output_tokens,
                    )
                    normalized_source = self._normalize_output(raw_source)
                    active_adapter.validate_model_output(normalized_source, target)
                    metadata = self.build_prompt_metadata(prompt_text)
                    return GeneratedTestArtifact(
                        target_id=target.target_id,
                        attempt_number=attempt_number,
                        prompt_text=prompt_text,
                        test_file_path=output_dir / self._artifact_name(target, attempt_number),
                        test_source=normalized_source,
                        prompt_version=str(metadata["prompt_version"]),
                        strategy=str(metadata["strategy"]),
                        generation_mode=generation_mode,
                        parent_attempt_number=parent_attempt_number,
                        provider_name=provider.name,
                        model_name=self.config.model_name,
                        language=active_adapter.language_id,
                        adapter_id=active_adapter.adapter_id,
                    )
                except (ProviderConfigurationError, ProviderResponseError, SyntaxError, ValueError) as exc:
                    fallback_reason = str(exc)
        fallback_artifact = active_adapter.generate_heuristic(
            target=target,
            prompt_text=prompt_text,
            output_dir=output_dir,
            attempt_number=attempt_number,
            generation_mode=generation_mode,
            parent_attempt_number=parent_attempt_number,
        )
        fallback_artifact.provider_name = "heuristic"
        fallback_artifact.model_name = None
        fallback_artifact.fallback_reason = fallback_reason
        fallback_artifact.language = active_adapter.language_id
        fallback_artifact.adapter_id = active_adapter.adapter_id
        return fallback_artifact

    def _should_use_llm(self) -> bool:
        return self.config.generator_mode in {"auto", "llm"}

    def _provider(self) -> LLMProvider | None:
        if self.provider is not None:
            return self.provider
        if not self.config.openai_api_key:
            return None
        self.provider = OpenAIResponsesProvider(api_key=self.config.openai_api_key)
        return self.provider

    def _provider_unavailable_reason(self) -> str:
        if self.config.generator_mode == "heuristic":
            return "Generator mode is heuristic."
        if not self.config.openai_api_key:
            return "OPENAI_API_KEY is not set."
        return "No provider is configured."

    def _adapter_for_target(self, target: FunctionTarget | ModuleTarget) -> LanguageAdapter:
        target_language = getattr(target, "language", self.adapter.language_id)
        if target_language == self.adapter.language_id:
            return self.adapter
        return get_adapter_for_language(self.config, target_language)

    @staticmethod
    def _artifact_name(target: FunctionTarget | ModuleTarget, attempt_number: int) -> str:
        suffix = target.file_path.suffix or ".txt"
        if suffix == ".go":
            if isinstance(target, FunctionTarget):
                return f"test_{target.file_path.stem}_{target.qualname.replace('.', '_')}_attempt_{attempt_number}_test.go"
            return f"test_{target.file_path.stem}_module_attempt_{attempt_number}_test.go"
        if isinstance(target, FunctionTarget):
            return f"test_{target.file_path.stem}_{target.qualname.replace('.', '_')}_attempt_{attempt_number}{suffix}"
        return f"test_{target.file_path.stem}_module_attempt_{attempt_number}{suffix}"

    @staticmethod
    def _normalize_output(raw_source: str) -> str:
        text = raw_source.strip()
        fence_match = re.fullmatch(r"```(?:[A-Za-z0-9_+-]+)?\s*(.*?)```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()
        return text
