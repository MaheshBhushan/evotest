from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from evotest.models import FunctionTarget, GeneratedTestArtifact, ModuleTarget, PromptContext


PROMPT_VERSION = "feedback-v3"
METADATA_START = "<evotest-metadata>"
METADATA_END = "</evotest-metadata>"


@dataclass(slots=True)
class ArgumentCandidate:
    expr: str
    value: object
    needs_tmp_path: bool = False
    needs_sample_file: bool = False


@dataclass(slots=True)
class CandidateCase:
    parameter_values: list[ArgumentCandidate] = field(default_factory=list)
    constructor_values: list[ArgumentCandidate] = field(default_factory=list)


@dataclass(slots=True)
class _PromptMetadata:
    prompt_version: str
    strategy: str
    previous_failures: list[str]
    uncovered_lines: list[int]


class PromptBuilder:
    version = PROMPT_VERSION

    def build(self, context: PromptContext) -> str:
        metadata = {
            "prompt_version": context.prompt_version,
            "strategy": context.strategy,
            "previous_failures": context.previous_failures,
            "uncovered_lines": context.uncovered_lines,
            "language": context.language,
            "adapter_id": context.adapter_id,
            "test_framework": context.test_framework,
        }
        sections = [
            self._section(
                "Task",
                "\n".join(
                    [
                        context.task_description,
                        f"- Prompt version: {context.prompt_version}",
                        f"- Strategy: {context.strategy}",
                        f"- Target: {context.target_id}",
                        f"- Kind: {context.target_kind}",
                        f"- Language: {context.language}",
                        f"- Test framework: {context.test_framework}",
                    ]
                ),
            ),
            self._section(
                "Target Contract",
                self._render_bullets(
                    context.target_contract
                    or [
                        f"Exercise `{context.target_id}` directly.",
                        "Do not replace the target with a local reimplementation.",
                    ]
                ),
            ),
            self._section(
                "Execution Constraints",
                self._render_bullets(
                    context.execution_constraints
                    or [
                        "Keep side effects local and deterministic.",
                        "Use strong assertions instead of smoke-test placeholders.",
                    ]
                ),
            ),
            self._section("Observed Imports", self._render_bullets(context.imports or ["none"])),
            self._section("Source Under Test", context.source_code.strip() or "none"),
            self._section("Nearby Context", context.nearby_context.strip() or "none"),
            self._section("Known Failures", self._known_failures_block(context)),
            self._section(
                "Execution Evidence",
                self._render_bullets(context.execution_evidence or ["No structured execution evidence yet."]),
            ),
            self._section("Coverage Goals", self._coverage_goals_block(context)),
            self._section("Reference Examples", self._reference_examples_block(context)),
            self._section("Output Contract", self._output_contract_block(context)),
        ]
        return (
            f"{METADATA_START}{json.dumps(metadata)}{METADATA_END}\n\n"
            + "\n\n".join(section for section in sections if section)
        )

    @staticmethod
    def _section(title: str, body: str) -> str:
        return f"{title}:\n{body.strip()}"

    @staticmethod
    def _render_bullets(items: list[str]) -> str:
        cleaned = [item.strip() for item in items if item and item.strip()]
        if not cleaned:
            return "- none"
        return "\n".join(f"- {item}" for item in cleaned)

    def _known_failures_block(self, context: PromptContext) -> str:
        observed_failures = self._render_bullets(context.previous_failures or ["none"])
        guidance = self._strategy_guidance(context.strategy, context.previous_failures)
        return (
            "Observed failures:\n"
            f"{observed_failures}\n\n"
            "What to do differently:\n"
            f"{self._render_bullets(guidance)}"
        )

    def _coverage_goals_block(self, context: PromptContext) -> str:
        uncovered = ", ".join(str(line) for line in context.uncovered_lines) if context.uncovered_lines else "none"
        goals = [
            f"Current uncovered lines: {uncovered}.",
            "Increase coverage only with distinct cases that execute new branches or behaviors.",
        ]
        if context.strategy == "coverage_expansion":
            goals.append("When uncovered lines point into branch bodies, choose inputs that flip those branches rather than reshaping the same case.")
        elif context.strategy == "assertion_repair":
            goals.append("Keep the same target path but replace weak or wrong assertions with case-specific expectations.")
        return self._render_bullets(goals)

    def _reference_examples_block(self, context: PromptContext) -> str:
        examples = context.reference_examples
        if not examples and context.existing_tests:
            examples = [context.existing_tests]
        if not examples:
            return "- none"
        rendered: list[str] = []
        for index, example in enumerate(examples, start=1):
            rendered.append(f"Example {index}:\n{example.strip()}")
        return "\n\n".join(rendered)

    def _output_contract_block(self, context: PromptContext) -> str:
        lines = list(context.output_contract)
        if context.output_instructions and context.output_instructions not in lines:
            lines.insert(0, context.output_instructions)
        return self._render_bullets(lines or ["Output only valid test code."])

    def _strategy_guidance(self, strategy: str, previous_failures: list[str]) -> list[str]:
        instructions = {
            "baseline": [
                "Generate the smallest meaningful passing test that directly exercises the requested target.",
                "Choose concrete inputs and assertions instead of existence-only checks.",
            ],
            "import_recovery": [
                "Keep the test intent, but repair import resolution so the real target module or symbol is loaded safely.",
                "Only add fallback import logic that is justified by the failures.",
            ],
            "argument_repair": [
                "Preserve the test shape, but repair inputs, fixtures, construction, or mocks so execution succeeds.",
                "Adjust argument values to match the target signature and obvious value constraints from the source.",
            ],
            "assertion_repair": [
                "Preserve the exercised path, but replace weak or wrong assertions with case-specific expectations.",
                "Do not collapse multiple cases into one broad assertion.",
            ],
            "coverage_expansion": [
                "Add distinct new cases that hit uncovered branches, comparisons, empty/non-empty paths, or truthy/falsy cases.",
                "Do not duplicate an existing case unless it now targets a different uncovered branch.",
            ],
            "safe_rebuild": [
                "Rebuild the file into a clean, collectable test file while preserving the intended target coverage.",
                "Prefer the simplest valid structure over clever repair logic when syntax or collection is broken.",
            ],
        }
        guidance = list(instructions.get(strategy, ["Generate valid test code for the target."]))
        for failure in previous_failures[-3:]:
            subtype, message = self._parse_failure(failure)
            extra_guidance = self._failure_guidance_for(subtype)
            if extra_guidance:
                guidance.append(f"{subtype}: {message}. {extra_guidance}")
        return guidance

    @staticmethod
    def _parse_failure(failure: str) -> tuple[str, str]:
        subtype, separator, message = failure.partition(":")
        if not separator:
            return failure.strip(), "previous attempt failed"
        return subtype.strip(), message.strip() or "previous attempt failed"

    @staticmethod
    def _failure_guidance_for(subtype: str) -> str:
        guidance = {
            "import_error": "Import the requested target from the correct module path and only use fallback loading if direct imports are the reason the last attempt failed.",
            "missing_dependency": "Remove unnecessary external dependencies or replace them with local fakes or fixtures.",
            "circular_import": "Delay or isolate imports so the target module can load without recursive import failures.",
            "type_error": "Repair the argument list, constructor inputs, or mock surface to match the target's actual call shape.",
            "value_error": "Choose inputs that satisfy obvious value constraints and boundary checks from the source.",
            "attribute_error": "Construct the right owner object or mock and ensure required attributes exist before invocation.",
            "path_error": "Use temporary files or directories instead of missing paths.",
            "bad_fixture": "Keep fixture setup minimal and deterministic.",
            "bad_parametrization": "Use distinct, valid cases instead of malformed or redundant parametrization.",
            "mock_misconfiguration": "Mock only the external dependency being exercised and keep the real target path intact.",
            "assertion_failure": "Keep the exercised path but change expected values to match the source behavior.",
            "equality_mismatch": "Compute per-case expected values instead of reusing one expectation everywhere.",
            "wrong_exception_type": "Assert the correct exception type and message only if the source clearly supports it.",
            "too_weak_oracle": "Replace smoke-test assertions with concrete output or side-effect checks.",
            "too_broad_assertion": "Narrow the assertion to the exact expected behavior for this case.",
            "float_tolerance_failure": "Use an appropriate tolerance assertion rather than exact equality.",
            "no_coverage_gain": "Add new inputs that flip uncovered conditions or hit uncovered lines instead of reshaping the same path.",
            "collection_error": "Return a clean, collectable test file with unique test names and valid structure.",
            "syntax_error": "Fix syntax only where needed and keep the target invocation intact.",
            "indentation_error": "Preserve the structure, but correct indentation so the file parses and collects.",
        }
        return guidance.get(subtype, "")


class StaticTestGenerator:
    def generate(
        self,
        target: FunctionTarget | ModuleTarget,
        prompt_text: str,
        output_dir: Path,
        attempt_number: int,
        *,
        generation_mode: str = "regenerate",
        parent_attempt_number: int | None = None,
    ) -> GeneratedTestArtifact:
        metadata = self._parse_prompt_metadata(prompt_text)
        if isinstance(target, FunctionTarget):
            file_name = self._function_test_filename(target, attempt_number)
            test_source = self._generate_function_test(target, metadata, attempt_number)
        else:
            file_name = self._module_test_filename(target, attempt_number)
            test_source = self._generate_module_test(target, metadata, attempt_number)
        self.validate_syntax(test_source)
        return GeneratedTestArtifact(
            target_id=target.target_id,
            attempt_number=attempt_number,
            prompt_text=prompt_text,
            test_file_path=output_dir / file_name,
            test_source=test_source,
            prompt_version=metadata.prompt_version,
            strategy=metadata.strategy,
            generation_mode=generation_mode,
            parent_attempt_number=parent_attempt_number,
        )

    def validate_syntax(self, test_source: str) -> None:
        ast.parse(test_source)

    def build_prompt_metadata(self, prompt_text: str) -> dict[str, object]:
        metadata = self._parse_prompt_metadata(prompt_text)
        return {
            "prompt_version": metadata.prompt_version,
            "strategy": metadata.strategy,
            "previous_failures": metadata.previous_failures,
            "uncovered_lines": metadata.uncovered_lines,
        }

    def render_import_recovery_block(self, target: FunctionTarget) -> str:
        relative_path = target.target_id.split("::", 1)[0]
        if target.is_method and target.owner_root_name:
            return (
                "import importlib.util\n"
                "from pathlib import Path\n\n"
                "def _load_target_owner():\n"
                f"    module_path = Path(__file__).resolve().parents[2] / {relative_path!r}\n"
                "    spec = importlib.util.spec_from_file_location('evotest_target_module', module_path)\n"
                "    assert spec is not None and spec.loader is not None\n"
                "    module = importlib.util.module_from_spec(spec)\n"
                "    spec.loader.exec_module(module)\n"
                f"    return module.{target.owner_root_name}\n\n"
                f"{target.owner_root_name} = _load_target_owner()\n"
            )
        return (
            "import importlib.util\n"
            "from pathlib import Path\n\n"
            "def _load_target_symbol():\n"
            f"    module_path = Path(__file__).resolve().parents[2] / {relative_path!r}\n"
            "    spec = importlib.util.spec_from_file_location('evotest_target_module', module_path)\n"
            "    assert spec is not None and spec.loader is not None\n"
            "    module = importlib.util.module_from_spec(spec)\n"
            "    spec.loader.exec_module(module)\n"
            f"    return module.{target.symbol_name}\n\n"
            f"{target.symbol_name} = _load_target_symbol()\n"
        )

    def render_single_function_case(
        self,
        target: FunctionTarget,
        *,
        case_name: str,
        case_values: list[ArgumentCandidate],
        constructor_values: list[ArgumentCandidate] | None,
        exact_expected: object | None,
        force_broad_assertion: bool,
    ) -> str:
        constructor_values = constructor_values or []
        use_tmp_path = any(candidate.needs_tmp_path for candidate in [*constructor_values, *case_values])
        signature = "()"
        prelude_lines: list[str] = []
        if use_tmp_path:
            prelude_lines.append(f"temp_root = Path(__file__).resolve().parent / '_evotest_tmp' / {case_name!r}")
            prelude_lines.append("temp_root.mkdir(parents=True, exist_ok=True)")
        if any(candidate.needs_sample_file for candidate in [*constructor_values, *case_values]):
            prelude_lines.append("sample_file = temp_root / 'sample.txt'")
            prelude_lines.append("sample_file.write_text('sample', encoding='utf-8')")
        invocation_setup, call_expr = self._invocation_plan(target, case_values, constructor_values)
        prelude_lines.extend(invocation_setup)
        if target.source.lstrip().startswith("async def"):
            invocation = f"result = asyncio.run({call_expr})"
        else:
            invocation = f"result = {call_expr}"
        assertion_lines = self._render_assertions(
            target=target,
            exact_expected=exact_expected,
            force_broad_assertion=force_broad_assertion,
        )
        body_lines = prelude_lines + [invocation, *assertion_lines]
        body = "\n".join(f"    {line}" for line in body_lines) if body_lines else "    pass"
        return f"def test_{case_name}{signature}:\n{body}\n"

    def _function_test_filename(self, target: FunctionTarget, attempt_number: int) -> str:
        safe_qualname = target.qualname.replace(".", "_")
        return f"test_{target.file_path.stem}_{safe_qualname}_attempt_{attempt_number}.py"

    def _module_test_filename(self, target: ModuleTarget, attempt_number: int) -> str:
        return f"test_{target.file_path.stem}_module_attempt_{attempt_number}.py"

    def _generate_function_test(
        self,
        target: FunctionTarget,
        metadata: _PromptMetadata,
        attempt_number: int,
    ) -> str:
        parameter_names = _extract_parameter_names(target.source)
        constructor_parameter_names = target.constructor_parameters if target.method_kind == "instance" else []
        candidate_cases = self._candidate_cases(target, constructor_parameter_names, parameter_names, metadata)
        imports = ["import pytest"]
        if any(
            candidate.needs_tmp_path
            for candidate_case in candidate_cases
            for candidate in [*candidate_case.constructor_values, *candidate_case.parameter_values]
        ):
            imports.append("from pathlib import Path")
        if target.source.lstrip().startswith("async def"):
            imports.append("import asyncio")
        use_import_fallback = metadata.strategy == "import_recovery" or any(
            "import_error" in failure or "missing_dependency" in failure or "circular_import" in failure
            for failure in metadata.previous_failures
        )
        if use_import_fallback:
            import_block = self.render_import_recovery_block(target)
        else:
            import_block = self._direct_import_block(target)
        cases: list[str] = []
        broad_assertion = metadata.strategy in {"coverage_expansion", "argument_repair"}
        for index, candidate_case in enumerate(candidate_cases, start=1):
            case_name = f"{target.qualname.replace('.', '_')}_attempt_{attempt_number}_case_{index}"
            value_map = {
                name: candidate.value
                for name, candidate in zip(parameter_names, candidate_case.parameter_values, strict=False)
            }
            exact_expected = _infer_expected_value(target.source, value_map)
            cases.append(
                self.render_single_function_case(
                    target,
                    case_name=case_name,
                    case_values=candidate_case.parameter_values,
                    constructor_values=candidate_case.constructor_values,
                    exact_expected=exact_expected,
                    force_broad_assertion=broad_assertion,
                )
            )
        return "\n".join(["\n".join(imports), import_block, *cases]).strip() + "\n"

    def _generate_module_test(
        self,
        target: ModuleTarget,
        metadata: _PromptMetadata,
        attempt_number: int,
    ) -> str:
        public_symbols = ", ".join(repr(symbol) for symbol in target.public_symbols) or ""
        if metadata.strategy == "import_recovery":
            module_import = (
                "import importlib.util\n"
                "from pathlib import Path\n\n"
                "def _load_module():\n"
                f"    module_path = Path(__file__).resolve().parents[2] / {target.target_id!r}\n"
                "    spec = importlib.util.spec_from_file_location('evotest_target_module', module_path)\n"
                "    assert spec is not None and spec.loader is not None\n"
                "    module = importlib.util.module_from_spec(spec)\n"
                "    spec.loader.exec_module(module)\n"
                "    return module\n"
            )
            load_line = "module = _load_module()"
        else:
            module_import = "import importlib\n"
            load_line = f"module = importlib.import_module({target.module_name!r})"
        return (
            f"{module_import}\n\n"
            f"def test_module_import_attempt_{attempt_number}():\n"
            f"    {load_line}\n"
            f"    assert module is not None\n"
            f"    public = [{public_symbols}]\n"
            f"    for name in public:\n"
            f"        assert hasattr(module, name)\n"
        )

    def _direct_import_block(self, target: FunctionTarget) -> str:
        if target.is_method and target.owner_root_name:
            return f"from {target.module_name} import {target.owner_root_name}\n"
        return f"from {target.module_name} import {target.symbol_name}\n"

    def _invocation_plan(
        self,
        target: FunctionTarget,
        case_values: list[ArgumentCandidate],
        constructor_values: list[ArgumentCandidate],
    ) -> tuple[list[str], str]:
        call_expr = ", ".join(candidate.expr for candidate in case_values)
        constructor_call = ", ".join(candidate.expr for candidate in constructor_values)
        if target.method_kind == "instance" and target.owner_qualname:
            return [f"subject = {target.owner_qualname}({constructor_call})"], f"subject.{target.symbol_name}({call_expr})"
        if target.method_kind in {"class", "static"} and target.owner_qualname:
            return [], f"{target.owner_qualname}.{target.symbol_name}({call_expr})"
        return [], f"{target.symbol_name}({call_expr})"

    def _candidate_cases(
        self,
        target: FunctionTarget,
        constructor_parameter_names: list[str],
        parameter_names: list[str],
        metadata: _PromptMetadata,
    ) -> list[CandidateCase]:
        if not parameter_names and not constructor_parameter_names:
            return [CandidateCase()]
        coverage_mode = metadata.strategy in {"coverage_expansion", "assertion_repair"}
        failure_tags = " ".join(metadata.previous_failures).lower()
        branch_candidates = self._branch_candidate_overrides(target, metadata, parameter_names, failure_tags)
        parameter_options = [
            self._candidate_values(
                name,
                coverage_mode=coverage_mode,
                failure_tags=failure_tags,
                branch_candidates=branch_candidates.get(name),
            )
            for name in parameter_names
        ]
        constructor_options = [
            self._candidate_values(name, coverage_mode=coverage_mode, failure_tags=failure_tags)
            for name in constructor_parameter_names
        ]
        base_case = CandidateCase(
            parameter_values=[options[0] for options in parameter_options],
            constructor_values=[options[0] for options in constructor_options],
        )
        cases = [base_case]
        if not coverage_mode:
            return cases
        seen = {self._case_key(base_case)}
        case_limit = 5
        for index, options in enumerate(parameter_options):
            for alternative in options[1:]:
                candidate_case = CandidateCase(
                    parameter_values=self._replace_case_value(base_case.parameter_values, index, alternative),
                    constructor_values=list(base_case.constructor_values),
                )
                case_key = self._case_key(candidate_case)
                if case_key in seen:
                    continue
                cases.append(candidate_case)
                seen.add(case_key)
                if len(cases) >= case_limit:
                    return cases
        for index, options in enumerate(constructor_options):
            for alternative in options[1:]:
                candidate_case = CandidateCase(
                    parameter_values=list(base_case.parameter_values),
                    constructor_values=self._replace_case_value(base_case.constructor_values, index, alternative),
                )
                case_key = self._case_key(candidate_case)
                if case_key in seen:
                    continue
                cases.append(candidate_case)
                seen.add(case_key)
                if len(cases) >= case_limit:
                    return cases
        return cases

    @staticmethod
    def _replace_case_value(
        values: list[ArgumentCandidate],
        index: int,
        alternative: ArgumentCandidate,
    ) -> list[ArgumentCandidate]:
        updated = list(values)
        updated[index] = alternative
        return updated

    @staticmethod
    def _case_key(candidate_case: CandidateCase) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (
            tuple(candidate.expr for candidate in candidate_case.parameter_values),
            tuple(candidate.expr for candidate in candidate_case.constructor_values),
        )

    def _candidate_values(
        self,
        parameter_name: str,
        *,
        coverage_mode: bool,
        failure_tags: str,
        branch_candidates: list[ArgumentCandidate] | None = None,
    ) -> list[ArgumentCandidate]:
        lowered = parameter_name.lower()
        defaults: list[ArgumentCandidate]
        if "path" in lowered or "file" in lowered:
            defaults = [
                ArgumentCandidate(expr="sample_file", value="sample", needs_tmp_path=True, needs_sample_file=True),
                ArgumentCandidate(expr="temp_root", value=".", needs_tmp_path=True),
            ]
        elif "flag" in lowered or lowered.startswith("is_") or lowered.startswith("has_"):
            defaults = [
                ArgumentCandidate(expr="False", value=False),
                ArgumentCandidate(expr="True", value=True),
            ]
        elif lowered in {"a", "b", "x", "y", "n", "num", "value", "amount"} or any(
            token in lowered for token in ("count", "size", "limit", "index", "offset", "age", "port")
        ):
            defaults = [
                ArgumentCandidate(expr="1", value=1),
                ArgumentCandidate(expr="2", value=2),
                ArgumentCandidate(expr="0", value=0),
                ArgumentCandidate(expr="-1", value=-1),
            ]
        elif "items" in lowered or lowered.endswith("list"):
            defaults = [
                ArgumentCandidate(expr="[]", value=[]),
                ArgumentCandidate(expr="[1, 2]", value=[1, 2]),
            ]
        elif "mapping" in lowered or "dict" in lowered:
            defaults = [
                ArgumentCandidate(expr="{}", value={}),
                ArgumentCandidate(expr="{'key': 'value'}", value={"key": "value"}),
            ]
        elif any(token in lowered for token in ("name", "text", "message", "label", "title", "left", "right")):
            defaults = [
                ArgumentCandidate(expr="'sample'", value="sample"),
                ArgumentCandidate(expr="''", value=""),
            ]
        elif "type_error" in failure_tags or "value_error" in failure_tags:
            defaults = [
                ArgumentCandidate(expr="1", value=1),
                ArgumentCandidate(expr="'sample'", value="sample"),
            ]
        else:
            defaults = [
                ArgumentCandidate(expr="1", value=1),
                ArgumentCandidate(expr="None", value=None),
                ArgumentCandidate(expr="'sample'", value="sample"),
            ]
        ordered = self._dedupe_candidates([*(branch_candidates or []), *defaults])
        return ordered[: min(len(ordered), 4 if coverage_mode else 1)]

    def _branch_candidate_overrides(
        self,
        target: FunctionTarget,
        metadata: _PromptMetadata,
        parameter_names: list[str],
        failure_tags: str,
    ) -> dict[str, list[ArgumentCandidate]]:
        if not parameter_names or not metadata.uncovered_lines:
            return {}
        local_uncovered = {
            line - target.line_start + 1
            for line in metadata.uncovered_lines
            if target.line_start <= line <= target.line_end
        }
        if not local_uncovered:
            return {}
        try:
            tree = ast.parse(target.source)
        except SyntaxError:
            return {}
        function_node = tree.body[0] if tree.body else None
        if not isinstance(function_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return {}
        overrides: dict[str, list[ArgumentCandidate]] = {}
        parameter_name_set = set(parameter_names)
        for node in ast.walk(function_node):
            if not isinstance(node, ast.If):
                continue
            body_hit = self._section_hits_uncovered(node.body, local_uncovered)
            else_hit = self._section_hits_uncovered(node.orelse, local_uncovered)
            if not body_hit and not else_hit:
                continue
            condition_candidates = self._candidates_for_condition(
                node.test,
                parameter_names=parameter_name_set,
                failure_tags=failure_tags,
                prefer_truthy=body_hit,
                prefer_falsy=else_hit,
            )
            for name, candidates in condition_candidates.items():
                overrides.setdefault(name, []).extend(candidates)
        return {
            name: self._dedupe_candidates(candidates)
            for name, candidates in overrides.items()
            if candidates
        }

    @staticmethod
    def _section_hits_uncovered(nodes: list[ast.stmt], local_uncovered: set[int]) -> bool:
        for statement in nodes:
            for subnode in ast.walk(statement):
                line_number = getattr(subnode, "lineno", None)
                if line_number in local_uncovered:
                    return True
        return False

    def _candidates_for_condition(
        self,
        test: ast.AST,
        *,
        parameter_names: set[str],
        failure_tags: str,
        prefer_truthy: bool,
        prefer_falsy: bool,
    ) -> dict[str, list[ArgumentCandidate]]:
        if isinstance(test, ast.Name) and test.id in parameter_names:
            return {
                test.id: self._truthiness_candidates(
                    test.id,
                    failure_tags=failure_tags,
                    prefer_truthy=prefer_truthy,
                    prefer_falsy=prefer_falsy,
                )
            }
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) and isinstance(test.operand, ast.Name):
            if test.operand.id in parameter_names:
                return {
                    test.operand.id: self._truthiness_candidates(
                        test.operand.id,
                        failure_tags=failure_tags,
                        prefer_truthy=prefer_falsy,
                        prefer_falsy=prefer_truthy,
                    )
                }
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and len(test.comparators) == 1:
            left = test.left
            comparator = test.comparators[0]
            if isinstance(left, ast.Name) and left.id in parameter_names and isinstance(comparator, ast.Constant):
                return {
                    left.id: self._comparison_candidates(
                        left.id,
                        test.ops[0],
                        comparator.value,
                        failure_tags=failure_tags,
                        prefer_truthy=prefer_truthy,
                        prefer_falsy=prefer_falsy,
                    )
                }
        return {}

    def _truthiness_candidates(
        self,
        parameter_name: str,
        *,
        failure_tags: str,
        prefer_truthy: bool,
        prefer_falsy: bool,
    ) -> list[ArgumentCandidate]:
        defaults = self._candidate_values(parameter_name, coverage_mode=True, failure_tags=failure_tags)
        truthy = [candidate for candidate in defaults if bool(candidate.value)]
        falsy = [candidate for candidate in defaults if not bool(candidate.value)]
        ordered: list[ArgumentCandidate] = []
        if prefer_truthy:
            ordered.extend(truthy)
        if prefer_falsy:
            ordered.extend(falsy)
        ordered.extend(defaults)
        return self._dedupe_candidates(ordered)

    def _comparison_candidates(
        self,
        parameter_name: str,
        operator: ast.cmpop,
        literal: object,
        *,
        failure_tags: str,
        prefer_truthy: bool,
        prefer_falsy: bool,
    ) -> list[ArgumentCandidate]:
        satisfying = self._candidate_for_comparison(parameter_name, operator, literal, satisfies=True, failure_tags=failure_tags)
        failing = self._candidate_for_comparison(parameter_name, operator, literal, satisfies=False, failure_tags=failure_tags)
        ordered: list[ArgumentCandidate] = []
        if prefer_truthy and satisfying is not None:
            ordered.append(satisfying)
        if prefer_falsy and failing is not None:
            ordered.append(failing)
        if satisfying is not None:
            ordered.append(satisfying)
        if failing is not None:
            ordered.append(failing)
        ordered.extend(self._candidate_values(parameter_name, coverage_mode=True, failure_tags=failure_tags))
        return self._dedupe_candidates(ordered)

    def _candidate_for_comparison(
        self,
        parameter_name: str,
        operator: ast.cmpop,
        literal: object,
        *,
        satisfies: bool,
        failure_tags: str,
    ) -> ArgumentCandidate | None:
        if isinstance(literal, bool):
            if isinstance(operator, (ast.Eq, ast.Is)):
                value = literal if satisfies else not literal
            elif isinstance(operator, (ast.NotEq, ast.IsNot)):
                value = not literal if satisfies else literal
            else:
                return None
            return self._candidate_from_value(value)
        if literal is None:
            if isinstance(operator, (ast.Eq, ast.Is)):
                value = None if satisfies else self._fallback_non_none_value(parameter_name, failure_tags)
            elif isinstance(operator, (ast.NotEq, ast.IsNot)):
                value = self._fallback_non_none_value(parameter_name, failure_tags) if satisfies else None
            else:
                return None
            return self._candidate_from_value(value)
        if isinstance(literal, (int, float)) and not isinstance(literal, bool):
            value = self._numeric_comparison_value(operator, literal, satisfies=satisfies)
            if value is None:
                return None
            return self._candidate_from_value(value)
        if isinstance(literal, str):
            if isinstance(operator, ast.Eq):
                value = literal if satisfies else (literal + "_alt" if literal else "sample")
            elif isinstance(operator, ast.NotEq):
                value = (literal + "_alt" if literal else "sample") if satisfies else literal
            else:
                return None
            return self._candidate_from_value(value)
        return None

    @staticmethod
    def _numeric_comparison_value(operator: ast.cmpop, literal: int | float, *, satisfies: bool) -> int | float | None:
        if isinstance(operator, ast.Gt):
            return literal + 1 if satisfies else literal
        if isinstance(operator, ast.GtE):
            return literal if satisfies else literal - 1
        if isinstance(operator, ast.Lt):
            return literal - 1 if satisfies else literal
        if isinstance(operator, ast.LtE):
            return literal if satisfies else literal + 1
        if isinstance(operator, ast.Eq):
            return literal if satisfies else literal + 1
        if isinstance(operator, ast.NotEq):
            return literal + 1 if satisfies else literal
        return None

    def _fallback_non_none_value(self, parameter_name: str, failure_tags: str) -> object:
        for candidate in self._candidate_values(parameter_name, coverage_mode=True, failure_tags=failure_tags):
            if candidate.value is not None:
                return candidate.value
        return "sample"

    @staticmethod
    def _candidate_from_value(value: object) -> ArgumentCandidate:
        if value is None:
            return ArgumentCandidate(expr="None", value=None)
        if value is True:
            return ArgumentCandidate(expr="True", value=True)
        if value is False:
            return ArgumentCandidate(expr="False", value=False)
        return ArgumentCandidate(expr=repr(value), value=value)

    @staticmethod
    def _dedupe_candidates(candidates: list[ArgumentCandidate]) -> list[ArgumentCandidate]:
        seen: set[str] = set()
        deduped: list[ArgumentCandidate] = []
        for candidate in candidates:
            if candidate.expr in seen:
                continue
            seen.add(candidate.expr)
            deduped.append(candidate)
        return deduped

    def _render_assertions(
        self,
        *,
        target: FunctionTarget,
        exact_expected: object | None,
        force_broad_assertion: bool,
    ) -> list[str]:
        if exact_expected is not None:
            return [f"assert result == {exact_expected!r}"]
        if _returns_none(target.source):
            return ["assert result is None"]
        if force_broad_assertion:
            return ["assert result is not None"]
        return ["assert result is not None"]

    def _parse_prompt_metadata(self, prompt_text: str) -> _PromptMetadata:
        match = re.search(f"{re.escape(METADATA_START)}(.*?){re.escape(METADATA_END)}", prompt_text, re.DOTALL)
        if not match:
            return _PromptMetadata(
                prompt_version=PROMPT_VERSION,
                strategy="baseline",
                previous_failures=[],
                uncovered_lines=[],
            )
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            payload = {}
        return _PromptMetadata(
            prompt_version=str(payload.get("prompt_version", PROMPT_VERSION)),
            strategy=str(payload.get("strategy", "baseline")),
            previous_failures=[str(item) for item in payload.get("previous_failures", [])],
            uncovered_lines=[int(item) for item in payload.get("uncovered_lines", [])],
        )


def _extract_parameter_names(function_source: str) -> list[str]:
    try:
        tree = ast.parse(function_source)
    except SyntaxError:
        return []
    node = tree.body[0] if tree.body else None
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return []
    parameter_names = []
    for arg in node.args.args:
        if arg.arg not in {"self", "cls"}:
            parameter_names.append(arg.arg)
    if node.args.vararg:
        parameter_names.append(node.args.vararg.arg)
    for arg in node.args.kwonlyargs:
        parameter_names.append(arg.arg)
    if node.args.kwarg:
        parameter_names.append(node.args.kwarg.arg)
    return parameter_names


def _infer_expected_value(function_source: str, values: dict[str, object]) -> object | None:
    try:
        tree = ast.parse(function_source)
    except SyntaxError:
        return None
    node = tree.body[0] if tree.body else None
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    statements = list(node.body)
    if statements and isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant) and isinstance(
        statements[0].value.value, str
    ):
        statements = statements[1:]
    if not statements:
        return None
    try:
        return _evaluate_statements(statements, values)
    except ValueError:
        return None


def _evaluate_statements(statements: list[ast.stmt], values: dict[str, object]) -> object:
    if not statements:
        raise ValueError("no statements")
    first = statements[0]
    if isinstance(first, ast.Return):
        if first.value is None:
            return None
        return _evaluate_expression(first.value, values)
    if isinstance(first, ast.If):
        condition = _evaluate_expression(first.test, values)
        if condition:
            return _evaluate_statements(first.body, values)
        fallback_statements = first.orelse or statements[1:]
        return _evaluate_statements(fallback_statements, values)
    raise ValueError(type(first).__name__)


def _evaluate_expression(node: ast.AST, values: dict[str, object]) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in values:
            raise ValueError(node.id)
        return values[node.id]
    if isinstance(node, ast.List):
        return [_evaluate_expression(item, values) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_evaluate_expression(item, values) for item in node.elts)
    if isinstance(node, ast.Dict):
        return {
            _evaluate_expression(key, values): _evaluate_expression(value, values)
            for key, value in zip(node.keys, node.values, strict=False)
        }
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate_expression(node.operand, values)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.Not):
            return not operand
        raise ValueError(type(node.op).__name__)
    if isinstance(node, ast.BinOp):
        left = _evaluate_expression(node.left, values)
        right = _evaluate_expression(node.right, values)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left**right
        raise ValueError(type(node.op).__name__)
    if isinstance(node, ast.BoolOp):
        values_evaluated = [_evaluate_expression(item, values) for item in node.values]
        if isinstance(node.op, ast.And):
            result = values_evaluated[0]
            for item in values_evaluated[1:]:
                result = result and item
            return result
        if isinstance(node.op, ast.Or):
            result = values_evaluated[0]
            for item in values_evaluated[1:]:
                result = result or item
            return result
        raise ValueError(type(node.op).__name__)
    if isinstance(node, ast.Compare):
        left = _evaluate_expression(node.left, values)
        for operator, comparator in zip(node.ops, node.comparators, strict=False):
            right = _evaluate_expression(comparator, values)
            if isinstance(operator, ast.Eq):
                ok = left == right
            elif isinstance(operator, ast.NotEq):
                ok = left != right
            elif isinstance(operator, ast.Lt):
                ok = left < right
            elif isinstance(operator, ast.LtE):
                ok = left <= right
            elif isinstance(operator, ast.Gt):
                ok = left > right
            elif isinstance(operator, ast.GtE):
                ok = left >= right
            elif isinstance(operator, ast.Is):
                ok = left is right
            elif isinstance(operator, ast.IsNot):
                ok = left is not right
            else:
                raise ValueError(type(operator).__name__)
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        condition = _evaluate_expression(node.test, values)
        branch = node.body if condition else node.orelse
        return _evaluate_expression(branch, values)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len" and len(node.args) == 1:
        return len(_evaluate_expression(node.args[0], values))
    raise ValueError(type(node).__name__)


def _returns_none(function_source: str) -> bool:
    try:
        tree = ast.parse(function_source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Return):
            return node.value is None or (isinstance(node.value, ast.Constant) and node.value.value is None)
    return False
