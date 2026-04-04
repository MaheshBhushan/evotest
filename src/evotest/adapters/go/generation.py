from __future__ import annotations

import json
import re
from pathlib import Path

from evotest.generator.engine import METADATA_END, METADATA_START, PROMPT_VERSION
from evotest.models import FunctionTarget, GeneratedTestArtifact, ModuleTarget


class GoHeuristicGenerator:
    language_id = "go"
    adapter_id = "go"
    package_name = "evotestgeneratedtests"

    def generate(
        self,
        *,
        target: FunctionTarget | ModuleTarget,
        prompt_text: str,
        output_dir: Path,
        attempt_number: int,
        generation_mode: str = "regenerate",
        parent_attempt_number: int | None = None,
    ) -> GeneratedTestArtifact:
        metadata = self._prompt_metadata(prompt_text)
        if isinstance(target, FunctionTarget):
            file_name = self._function_test_filename(target, attempt_number)
            test_source = self._generate_function_test(target, metadata, attempt_number)
        else:
            file_name = self._module_test_filename(target, attempt_number)
            test_source = self._generate_module_test(target, attempt_number)
        return GeneratedTestArtifact(
            target_id=target.target_id,
            attempt_number=attempt_number,
            prompt_text=prompt_text,
            test_file_path=output_dir / file_name,
            test_source=test_source,
            prompt_version=str(metadata["prompt_version"]),
            strategy=str(metadata["strategy"]),
            generation_mode=generation_mode,
            parent_attempt_number=parent_attempt_number,
            language=self.language_id,
            adapter_id=self.adapter_id,
        )

    def _generate_function_test(
        self,
        target: FunctionTarget,
        metadata: dict[str, object],
        attempt_number: int,
    ) -> str:
        package_alias = "targetpkg"
        parameter_names = self._parameter_names(target.source)
        args = self._candidate_args(parameter_names)
        expected = self._infer_expected(target.source, parameter_names, args)
        imports = ['"testing"', f'{package_alias} "{target.module_name}"']
        if expected is None or (metadata.get("strategy") == "coverage_expansion" and args):
            imports.append('"reflect"')
        assertion = self._assertion_block(expected)
        body = [
            f"package {self.package_name}\n\n",
            "import (\n",
            *[f"\t{entry}\n" for entry in imports],
            ")\n\n",
            f"func Test{self._test_name(target, attempt_number, 1)}(t *testing.T) {{\n",
            *self._invocation_lines(target, package_alias, args, assertion),
            "}\n",
        ]
        if metadata.get("strategy") == "coverage_expansion" and args:
            alternate_args = list(args)
            alternate_args[0] = self._alternate_arg(alternate_args[0])
            body.extend(
                [
                    "\n",
                    f"func Test{self._test_name(target, attempt_number, 2)}(t *testing.T) {{\n",
                    *self._invocation_lines(target, package_alias, alternate_args, self._assertion_block(None)),
                    "}\n",
                ]
            )
        return "".join(body)

    def _generate_module_test(self, target: ModuleTarget, attempt_number: int) -> str:
        package_alias = "targetpkg"
        body = [
            f"package {self.package_name}\n\n",
            "import (\n",
            '\t"testing"\n',
            f'\t{package_alias} "{target.module_name}"\n',
            ")\n\n",
            f"func TestModuleImportAttempt{attempt_number}(t *testing.T) {{\n",
        ]
        if target.public_symbols:
            body.append(f"\t_ = {package_alias}.{target.public_symbols[0]}\n")
        else:
            body.append('\tt.Skip("no public symbols discovered for module target")\n')
        body.append("}\n")
        return "".join(body)

    @staticmethod
    def _function_test_filename(target: FunctionTarget, attempt_number: int) -> str:
        safe_qualname = target.qualname.replace(".", "_")
        return f"test_{target.file_path.stem}_{safe_qualname}_attempt_{attempt_number}_test.go"

    @staticmethod
    def _module_test_filename(target: ModuleTarget, attempt_number: int) -> str:
        return f"test_{target.file_path.stem}_module_attempt_{attempt_number}_test.go"

    @staticmethod
    def _test_name(target: FunctionTarget, attempt_number: int, case_number: int) -> str:
        base = target.qualname.replace(".", "")
        return f"{base}Attempt{attempt_number}Case{case_number}"

    def _invocation_lines(
        self,
        target: FunctionTarget,
        package_alias: str,
        args: list[str],
        assertion: list[str],
    ) -> list[str]:
        invocation = self._call_expression(target, package_alias, args)
        lines: list[str] = []
        if target.is_method and target.owner_qualname:
            lines.append(f"\tvar subject {package_alias}.{target.owner_qualname}\n")
        lines.append(f"\tgot := {invocation}\n")
        lines.extend(f"\t{line}\n" for line in assertion)
        return lines

    @staticmethod
    def _call_expression(target: FunctionTarget, package_alias: str, args: list[str]) -> str:
        call_args = ", ".join(args)
        if target.is_method and target.owner_qualname:
            return f"subject.{target.symbol_name}({call_args})"
        return f"{package_alias}.{target.symbol_name}({call_args})"

    def _assertion_block(self, expected: object | None) -> list[str]:
        if isinstance(expected, str):
            return [
                f'if got != "{expected}" {{',
                f'\tt.Fatalf("expected %q, got %q", "{expected}", got)',
                "}",
            ]
        if isinstance(expected, bool):
            bool_literal = "true" if expected else "false"
            return [
                f"if got != {bool_literal} {{",
                f'\tt.Fatalf("expected {bool_literal}, got %v", got)',
                "}",
            ]
        if isinstance(expected, (int, float)):
            return [
                f"if got != {expected} {{",
                f'\tt.Fatalf("expected {expected}, got %v", got)',
                "}",
            ]
        return [
            "if reflect.ValueOf(got).IsZero() {",
            '\tt.Fatalf("expected non-zero result, got %v", got)',
            "}",
        ]

    def _parameter_names(self, function_source: str) -> list[str]:
        match = re.search(r"func\s+(?:\([^)]+\)\s*)?[A-Za-z_][A-Za-z0-9_]*\s*\((?P<params>[^)]*)\)", function_source)
        if match is None:
            return []
        params = []
        for raw_param in match.group("params").split(","):
            raw_param = raw_param.strip()
            if not raw_param:
                continue
            name = raw_param.split()[0]
            if name not in {"", "_"}:
                params.append(name)
        return params

    @staticmethod
    def _candidate_args(parameter_names: list[str]) -> list[str]:
        args: list[str] = []
        for name in parameter_names:
            lowered = name.lower()
            if "flag" in lowered or lowered.startswith("is"):
                args.append("false")
            elif any(token in lowered for token in ("name", "text", "message", "label", "title")):
                args.append('"sample"')
            elif "path" in lowered or "file" in lowered:
                args.append('"go.mod"')
            else:
                args.append("1")
        return args

    @staticmethod
    def _alternate_arg(arg: str) -> str:
        if arg == "false":
            return "true"
        if arg == "1":
            return "0"
        if arg == '"sample"':
            return '""'
        return arg

    def _infer_expected(self, function_source: str, parameter_names: list[str], args: list[str]) -> object | None:
        normalized = re.sub(r"\s+", " ", function_source)
        if len(parameter_names) >= 2 and len(args) >= 2:
            left, right = parameter_names[0], parameter_names[1]
            if re.search(rf"return {re.escape(left)} \+ {re.escape(right)}", normalized):
                try:
                    return int(args[0]) + int(args[1])
                except ValueError:
                    return None
        if len(parameter_names) >= 1 and len(args) >= 1:
            conditional = re.search(
                rf'if {re.escape(parameter_names[0])} \{{ return "([^"]*)" \}} return "([^"]*)"',
                normalized,
            )
            if conditional is not None:
                return conditional.group(1) if args[0] == "true" else conditional.group(2)
            negated_conditional = re.search(
                rf'if !{re.escape(parameter_names[0])} \{{ return "([^"]*)" \}} return "([^"]*)"',
                normalized,
            )
            if negated_conditional is not None:
                return negated_conditional.group(2) if args[0] == "true" else negated_conditional.group(1)
            if re.search(rf'return "([^"]*)" \+ {re.escape(parameter_names[0])}', normalized):
                prefix = re.search(rf'return "([^"]*)" \+ {re.escape(parameter_names[0])}', normalized)
                if prefix is not None:
                    return prefix.group(1) + args[0].strip('"')
        literal = re.search(r'return "([^"]*)"', normalized)
        if literal is not None:
            return literal.group(1)
        if re.search(r"\breturn true\b", normalized):
            return True
        if re.search(r"\breturn false\b", normalized):
            return False
        numeric = re.search(r"\breturn (-?\d+)\b", normalized)
        if numeric is not None:
            return int(numeric.group(1))
        return None

    @staticmethod
    def _prompt_metadata(prompt_text: str) -> dict[str, object]:
        match = re.search(f"{re.escape(METADATA_START)}(.*?){re.escape(METADATA_END)}", prompt_text, re.DOTALL)
        if match is None:
            return {"prompt_version": PROMPT_VERSION, "strategy": "baseline"}
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            payload = {}
        return {
            "prompt_version": str(payload.get("prompt_version", PROMPT_VERSION)),
            "strategy": str(payload.get("strategy", "baseline")),
        }
