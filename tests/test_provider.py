from evotest.generator.provider import OpenAIResponsesProvider


def test_openai_provider_uses_python_specific_instructions() -> None:
    prompt = "- Language: python\n- Test framework: pytest\n"

    instructions = OpenAIResponsesProvider._instructions_for_prompt(prompt)

    assert instructions == "Return only valid Python pytest source code. Do not include prose, markdown fences, or explanations."


def test_openai_provider_uses_javascript_specific_instructions() -> None:
    prompt = "- Language: javascript\n- Test framework: vitest\n"

    instructions = OpenAIResponsesProvider._instructions_for_prompt(prompt)

    assert "JavaScript/TypeScript vitest test source code" in instructions


def test_openai_provider_uses_go_specific_instructions() -> None:
    prompt = "- Language: go\n- Test framework: go test\n"

    instructions = OpenAIResponsesProvider._instructions_for_prompt(prompt)

    assert instructions == "Return only valid Go _test.go source code. Do not include prose, markdown fences, or explanations."


def test_openai_provider_can_accept_explicit_instruction_override() -> None:
    prompt = "- Language: python\n- Test framework: pytest\n"

    instructions = "Return only valid Python source code for the full updated source file."

    assert instructions != OpenAIResponsesProvider._instructions_for_prompt(prompt)


class _FakeResponsesClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if "temperature" in kwargs:
            raise RuntimeError("Unsupported parameter: 'temperature' is not supported with this model.")
        return type("Resp", (), {"output_text": "def test_ok():\n    assert True\n"})()


def test_openai_provider_retries_without_temperature_when_model_rejects_it() -> None:
    provider = OpenAIResponsesProvider(api_key="test-key")
    client = _FakeResponsesClient()
    provider._client = type("Client", (), {"responses": client})()

    output = provider.generate_text(
        "- Language: python\n- Test framework: pytest\n",
        model="gpt-5.4",
        temperature=0.2,
        max_output_tokens=4000,
    )

    assert "def test_ok()" in output
    assert len(client.calls) == 2
    assert "temperature" in client.calls[0]
    assert "temperature" not in client.calls[1]
