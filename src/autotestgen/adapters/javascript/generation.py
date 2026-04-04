from __future__ import annotations

import json
import re
from pathlib import Path

from autotestgen.generator.engine import METADATA_END, METADATA_START, PROMPT_VERSION
from autotestgen.models import FunctionTarget, GeneratedTestArtifact, ModuleTarget


class JavaScriptHeuristicGenerator:
    language_id = "javascript"
    adapter_id = "javascript"
    _extensions = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

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
        file_name = self._artifact_name(target, attempt_number)
        if isinstance(target, FunctionTarget):
            test_source = self._generate_function_test(target, metadata, attempt_number)
        else:
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
        framework = self._framework()
        target_loader = self._module_loader(target.file_path, symbol_name=target.symbol_name)
        params = self._parameter_names(target.source)
        args = [candidate for candidate in self._candidate_values(params)]
        expectation = self._expected_expression(target, params, args)
        framework_import = ""
        if framework == "vitest":
            framework_import = "import { beforeAll, describe, test, expect } from 'vitest';\n"
        body = [
            framework_import,
            "import path from 'node:path';\nimport { pathToFileURL } from 'node:url';\n",
            f"{self._export_resolver_source()}\n",
            f"let {target.symbol_name};\n",
            "beforeAll(async () => {\n",
            f"{target_loader}",
            "});\n\n",
            f"describe('{target.symbol_name}', () => {{\n",
            f"  test('{target.symbol_name} attempt {attempt_number} case 1', () => {{\n",
            f"    const result = {target.symbol_name}({', '.join(args)});\n",
            f"    {expectation}\n",
            "  });\n",
            "});\n",
        ]
        if metadata.get("strategy") == "coverage_expansion" and params:
            alternate_args = list(args)
            alternate_args[0] = self._alternate_value(alternate_args[0])
            body.extend(
                [
                    "\n",
                    f"test('{target.symbol_name} attempt {attempt_number} case 2', () => {{\n",
                    f"  const result = {target.symbol_name}({', '.join(alternate_args)});\n",
                    "  expect(result).not.toBeUndefined();\n",
                    "});\n",
                ]
            )
        return "".join(body)

    def _generate_module_test(self, target: ModuleTarget, attempt_number: int) -> str:
        framework = self._framework()
        framework_import = "import { beforeAll, describe, test, expect } from 'vitest';\n" if framework == "vitest" else ""
        loader = self._module_loader(target.file_path, module_var="targetModule")
        exports_assertions = "\n".join(
            f"  expect(__autotestgenResolveExport(targetModule, {symbol!r})).toBeDefined();"
            for symbol in (target.public_symbols or ["default"])
        )
        return (
            f"{framework_import}import path from 'node:path';\n"
            "import { pathToFileURL } from 'node:url';\n\n"
            f"{self._export_resolver_source()}\n"
            "let targetModule;\n\n"
            "beforeAll(async () => {\n"
            f"{loader}"
            "});\n\n"
            f"test('module import attempt {attempt_number}', () => {{\n"
            "  expect(targetModule).toBeDefined();\n"
            f"{exports_assertions}\n"
            "});\n"
        )

    def _module_loader(
        self,
        file_path: Path,
        *,
        symbol_name: str | None = None,
        module_var: str = "loadedModule",
    ) -> str:
        relative_path = file_path.relative_to(self.project_root).as_posix()
        lines = [
            f"  const moduleUrl = pathToFileURL(path.resolve(process.cwd(), {relative_path!r})).href;\n",
            f"  const {module_var} = await import(moduleUrl);\n",
        ]
        if symbol_name is not None:
            lines.extend(
                [
                    f"  {symbol_name} = __autotestgenResolveExport({module_var}, {symbol_name!r});\n",
                    f"  if ({symbol_name} === undefined) {{\n",
                    f"    throw new Error('Unable to resolve export {symbol_name} from {relative_path}');\n",
                    "  }\n",
                ]
            )
        return "".join(lines)

    @staticmethod
    def _export_resolver_source() -> str:
        return (
            "function __autotestgenResolveExport(loadedModule, exportName) {\n"
            "  if (loadedModule == null) {\n"
            "    return undefined;\n"
            "  }\n"
            "  if (Object.prototype.hasOwnProperty.call(loadedModule, exportName)) {\n"
            "    return loadedModule[exportName];\n"
            "  }\n"
            "  const defaultExport = loadedModule.default;\n"
            "  if (defaultExport && typeof defaultExport === 'object' && exportName in defaultExport) {\n"
            "    return defaultExport[exportName];\n"
            "  }\n"
            "  if (typeof defaultExport === 'function') {\n"
            "    return defaultExport;\n"
            "  }\n"
            "  return undefined;\n"
            "}\n"
        )

    @staticmethod
    def _artifact_name(target: FunctionTarget | ModuleTarget, attempt_number: int) -> str:
        suffix = target.file_path.suffix if target.file_path.suffix in JavaScriptHeuristicGenerator._extensions else ".js"
        if isinstance(target, FunctionTarget):
            return f"test_{target.file_path.stem}_{target.qualname.replace('.', '_')}_attempt_{attempt_number}{suffix}"
        return f"test_{target.file_path.stem}_module_attempt_{attempt_number}{suffix}"

    @staticmethod
    def _parameter_names(source: str) -> list[str]:
        patterns = (
            re.compile(r"function\s+[A-Za-z_][A-Za-z0-9_]*\s*\((?P<params>[^)]*)\)"),
            re.compile(r"[A-Za-z_][A-Za-z0-9_]*\s*=\s*(?:async\s*)?\((?P<params>[^)]*)\)\s*=>"),
        )
        for pattern in patterns:
            match = pattern.search(source)
            if match:
                return [param.strip() for param in match.group("params").split(",") if param.strip()]
        return []

    @staticmethod
    def _candidate_values(parameter_names: list[str]) -> list[str]:
        values: list[str] = []
        for name in parameter_names:
            lowered = name.lower()
            if "flag" in lowered or lowered.startswith("is"):
                values.append("false")
            elif "path" in lowered or "file" in lowered:
                values.append("path.resolve(process.cwd(), 'package.json')")
            elif any(token in lowered for token in ("name", "text", "label", "title", "message")):
                values.append("'sample'")
            else:
                values.append("1")
        return values

    @staticmethod
    def _alternate_value(value: str) -> str:
        if value == "false":
            return "true"
        if value == "1":
            return "0"
        if value == "'sample'":
            return "''"
        return "undefined"

    @staticmethod
    def _expected_expression(target: FunctionTarget, params: list[str], args: list[str]) -> str:
        source = target.source.replace("\n", " ")
        if len(params) >= 2 and re.search(rf"return\s+{re.escape(params[0])}\s*\+\s*{re.escape(params[1])}", source):
            if args[:2] == ["1", "1"]:
                return "expect(result).toBe(2);"
        return "expect(result).not.toBeUndefined();"

    def _framework(self) -> str:
        package_json = self.project_root / "package.json"
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
