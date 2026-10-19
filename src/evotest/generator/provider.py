from __future__ import annotations

from typing import Protocol


class ProviderConfigurationError(RuntimeError):
    """Raised when the configured provider cannot be initialized."""


class ProviderResponseError(RuntimeError):
    """Raised when the provider request fails or returns unusable output."""


class LLMProvider(Protocol):
    name: str

    def generate_text(
        self,
        prompt_text: str,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        instructions: str | None = None,
    ) -> str: ...


class OpenAIResponsesProvider:
    name = "openai"

    def __init__(self, *, api_key: str | None) -> None:
        if not api_key:
            raise ProviderConfigurationError("OPENAI_API_KEY is not set.")
        self._api_key = api_key
        self._client = None
        self.usage_records: list[dict[str, int] | None] = []

    def generate_text(
        self,
        prompt_text: str,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        instructions: str | None = None,
    ) -> str:
        client = self._client_instance()
        request_kwargs = {
            "model": model,
            "input": prompt_text,
            "instructions": instructions or self._instructions_for_prompt(prompt_text),
            "max_output_tokens": max_output_tokens,
            "reasoning": {"effort": "low"},
        }
        self.usage_records.append(None)
        try:
            response = client.responses.create(
                temperature=temperature,
                **request_kwargs,
            )
        except Exception as exc:
            if self._is_unsupported_temperature_error(exc):
                try:
                    response = client.responses.create(**request_kwargs)
                except Exception as retry_exc:
                    raise ProviderResponseError(f"OpenAI request failed: {retry_exc}") from retry_exc
            else:
                raise ProviderResponseError(f"OpenAI request failed: {exc}") from exc
        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens = getattr(usage, "input_tokens", None)
            output_tokens = getattr(usage, "output_tokens", None)
            if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                self.usage_records[-1] = {"input_tokens": input_tokens, "output_tokens": output_tokens}
        output_text = getattr(response, "output_text", "") or ""
        if not output_text.strip():
            raise ProviderResponseError("OpenAI returned empty output.")
        return output_text

    @staticmethod
    def _instructions_for_prompt(prompt_text: str) -> str:
        language_match = None
        framework_match = None
        for line in prompt_text.splitlines():
            if line.startswith("- Language: "):
                language_match = line.removeprefix("- Language: ").strip().lower()
            elif line.startswith("- Test framework: "):
                framework_match = line.removeprefix("- Test framework: ").strip().lower()
        framework = framework_match or "tests"
        if language_match == "python":
            return "Return only valid Python pytest source code. Do not include prose, markdown fences, or explanations."
        if language_match == "javascript":
            return (
                f"Return only valid JavaScript/TypeScript {framework} test source code. "
                "Do not include prose, markdown fences, or explanations."
            )
        if language_match == "go":
            return "Return only valid Go _test.go source code. Do not include prose, markdown fences, or explanations."
        return f"Return only valid {framework} test source code. Do not include prose, markdown fences, or explanations."

    def _client_instance(self):
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderConfigurationError("The openai package is not installed.") from exc
        self._client = OpenAI(api_key=self._api_key)
        return self._client

    @staticmethod
    def _is_unsupported_temperature_error(exc: Exception) -> bool:
        message = str(exc).lower()
        return "unsupported parameter" in message and "temperature" in message
