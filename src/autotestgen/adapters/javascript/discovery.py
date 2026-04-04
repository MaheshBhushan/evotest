from __future__ import annotations

import re
from pathlib import Path

from autotestgen.models import FunctionTarget, ModuleTarget, ProjectInventory


class JavaScriptProjectAnalyzer:
    language_id = "javascript"
    adapter_id = "javascript"
    _extensions = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def discover(self) -> ProjectInventory:
        functions: list[FunctionTarget] = []
        modules: list[ModuleTarget] = []
        for file_path in self._source_files():
            source = self._read_text(file_path)
            if source is None:
                continue
            relative_path = file_path.relative_to(self.project_root).as_posix()
            public_symbols = list(self._exported_functions(source).keys())
            modules.append(
                ModuleTarget(
                    target_id=relative_path,
                    file_path=file_path,
                    module_name=self._module_name(relative_path),
                    source=source,
                    public_symbols=public_symbols,
                    complexity_score=self._complexity_score(source),
                    io_hint=self._contains_io_hint(source),
                    target_score=self._score_module(public_symbols, source),
                    language=self.language_id,
                    adapter_id=self.adapter_id,
                )
            )
            for name, entry in self._exported_functions(source).items():
                functions.append(
                    FunctionTarget(
                        target_id=f"{relative_path}::{name}",
                        file_path=file_path,
                        module_name=self._module_name(relative_path),
                        symbol_name=name,
                        qualname=name,
                        line_start=entry["line_start"],
                        line_end=entry["line_end"],
                        source=entry["source"],
                        dependency_hints=sorted(set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", entry["source"]))),
                        complexity_score=self._complexity_score(entry["source"]),
                        io_hint=self._contains_io_hint(entry["source"]),
                        target_score=self._score_function(name, entry["source"]),
                        language=self.language_id,
                        adapter_id=self.adapter_id,
                    )
                )
        functions.sort(key=lambda target: (-target.target_score, target.target_id))
        modules.sort(key=lambda target: (-target.target_score, target.target_id))
        return ProjectInventory(
            functions=functions,
            modules=modules,
            language=self.language_id,
            adapter_id=self.adapter_id,
        )

    def find_target(
        self,
        target_ref: str,
        inventory: ProjectInventory | None = None,
    ) -> FunctionTarget | ModuleTarget | None:
        normalized = target_ref.replace("\\", "/")
        inventory = inventory or self.discover()
        for function in inventory.functions:
            if function.target_id == normalized or f"{function.file_path}::{function.qualname}" == target_ref:
                return function
        for module in inventory.modules:
            if module.target_id == normalized or str(module.file_path) == target_ref:
                return module
        return None

    def _source_files(self) -> list[Path]:
        return sorted(
            path
            for path in self.project_root.rglob("*")
            if path.is_file()
            and path.suffix in self._extensions
            and "node_modules" not in path.parts
            and ".autotestgen" not in path.parts
            and "coverage" not in path.parts
            and "dist" not in path.parts
            and "build" not in path.parts
            and "tests" not in path.parts
            and ".test." not in path.name
            and ".spec." not in path.name
        )

    @staticmethod
    def _read_text(file_path: Path) -> str | None:
        try:
            return file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def _module_name(relative_path: str) -> str:
        return relative_path.rsplit(".", 1)[0].replace("/", ".")

    def _exported_functions(self, source: str) -> dict[str, dict[str, str | int]]:
        local_functions = self._local_function_definitions(source)
        exported: dict[str, dict[str, str | int]] = {}

        direct_export_patterns = (
            re.compile(r"export\s+(?:async\s+)?function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
            re.compile(r"export\s+default\s+(?:async\s+)?function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
            re.compile(
                r"export\s+(?:const|let|var)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>",
                re.MULTILINE,
            ),
            re.compile(
                r"export\s+(?:const|let|var)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?function(?:\s+[A-Za-z_][A-Za-z0-9_]*)?\s*\(",
                re.MULTILINE,
            ),
        )
        for pattern in direct_export_patterns:
            for match in pattern.finditer(source):
                self._merge_export(exported, match.group("name"), match.group("name"), local_functions)

        for match in re.finditer(r"export\s+default\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b", source, re.MULTILINE):
            self._merge_export(exported, match.group("name"), match.group("name"), local_functions)

        for match in re.finditer(r"module\.exports\s*=\s*\{(?P<body>.*?)\}", source, re.DOTALL):
            for entry in self._object_export_entries(match.group("body")):
                export_name, local_name = entry
                self._merge_export(exported, export_name, local_name, local_functions)

        for match in re.finditer(
            r"module\.exports\s*=\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*;?",
            source,
            re.MULTILINE,
        ):
            self._merge_export(exported, match.group("name"), match.group("name"), local_functions)

        property_export_patterns = (
            re.compile(
                r"(?:module\.)?exports\.(?P<export>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<local>[A-Za-z_][A-Za-z0-9_]*)",
                re.MULTILINE,
            ),
        )
        for pattern in property_export_patterns:
            for match in pattern.finditer(source):
                self._merge_export(exported, match.group("export"), match.group("local"), local_functions)

        return exported

    def _local_function_definitions(self, source: str) -> dict[str, dict[str, str | int]]:
        functions: dict[str, dict[str, str | int]] = {}
        patterns = (
            re.compile(
                r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
                re.MULTILINE,
            ),
            re.compile(
                r"(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*=>",
                re.MULTILINE,
            ),
            re.compile(
                r"(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?function(?:\s+[A-Za-z_][A-Za-z0-9_]*)?\s*\(",
                re.MULTILINE,
            ),
        )
        for pattern in patterns:
            for match in pattern.finditer(source):
                name = match.group("name")
                snippet, line_start, line_end = self._extract_source_block(source, match.start())
                functions[name] = {
                    "source": snippet,
                    "line_start": line_start,
                    "line_end": line_end,
                }
        return functions

    @staticmethod
    def _object_export_entries(body: str) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        for raw_entry in body.split(","):
            entry = raw_entry.strip()
            if not entry:
                continue
            if ":" in entry:
                export_name, local_name = [part.strip() for part in entry.split(":", 1)]
            else:
                export_name = local_name = entry
            export_name = export_name.split()[-1]
            local_name = local_name.split()[-1]
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", export_name) and re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*",
                local_name,
            ):
                entries.append((export_name, local_name))
        return entries

    @staticmethod
    def _merge_export(
        exported: dict[str, dict[str, str | int]],
        export_name: str,
        local_name: str,
        local_functions: dict[str, dict[str, str | int]],
    ) -> None:
        entry = local_functions.get(local_name) or local_functions.get(export_name)
        if entry is None:
            return
        exported[export_name] = entry

    @staticmethod
    def _extract_source_block(source: str, start_index: int) -> tuple[str, int, int]:
        line_start = source.count("\n", 0, start_index) + 1
        brace_index = source.find("{", start_index)
        if brace_index == -1:
            end_index = source.find("\n", start_index)
            if end_index == -1:
                end_index = len(source)
            snippet = source[start_index:end_index]
            return snippet, line_start, line_start
        depth = 0
        end_index = len(source)
        for index in range(brace_index, len(source)):
            char = source[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end_index = index + 1
                    break
        snippet = source[start_index:end_index]
        line_end = line_start + snippet.count("\n")
        return snippet, line_start, line_end

    @staticmethod
    def _complexity_score(source: str) -> int:
        tokens = ("if ", "for ", "while ", "switch ", "catch ", "&&", "||", "?")
        return sum(source.count(token) for token in tokens)

    @staticmethod
    def _contains_io_hint(source: str) -> bool:
        lowered = source.lower()
        return any(
            marker in lowered
            for marker in ("fs.", "readfile", "writefile", "fetch(", "http.", "https.", "path.", "child_process")
        )

    def _score_function(self, name: str, source: str) -> float:
        score = 4.0 if not name.startswith("_") else 1.0
        score += min(len(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", source)), 12) * 0.15
        score += min(self._complexity_score(source), 15) * 0.5
        if self._contains_io_hint(source):
            score += 1.5
        return round(score, 2)

    def _score_module(self, public_symbols: list[str], source: str) -> float:
        score = float(len(public_symbols)) * 0.75
        score += min(self._complexity_score(source), 20) * 0.35
        if self._contains_io_hint(source):
            score += 1.0
        return round(score, 2)
