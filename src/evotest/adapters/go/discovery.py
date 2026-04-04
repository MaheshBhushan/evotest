from __future__ import annotations

import re
from pathlib import Path

from evotest.models import FunctionTarget, ImportRef, ModuleTarget, ProjectInventory


class GoProjectAnalyzer:
    language_id = "go"
    adapter_id = "go"

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.module_path = self._module_path()

    def discover(self) -> ProjectInventory:
        functions: list[FunctionTarget] = []
        modules: list[ModuleTarget] = []
        for file_path in self._go_files():
            source = self._read_text(file_path)
            if source is None:
                continue
            relative_path = file_path.relative_to(self.project_root).as_posix()
            module_name = self._package_import_path(file_path)
            package_name = self._package_name(source)
            module_targets = self._module_public_symbols(source)
            modules.append(
                ModuleTarget(
                    target_id=relative_path,
                    file_path=file_path,
                    module_name=module_name,
                    source=source,
                    imports=self._extract_imports(source),
                    public_symbols=module_targets,
                    complexity_score=self._complexity_score(source),
                    io_hint=self._contains_io_hint(source),
                    target_score=self._score_module(module_targets, source),
                    language=self.language_id,
                    adapter_id=self.adapter_id,
                )
            )
            for function in self._functions(source):
                qualname = function["qualname"]
                symbol_name = function["symbol_name"]
                functions.append(
                    FunctionTarget(
                        target_id=f"{relative_path}::{qualname}",
                        file_path=file_path,
                        module_name=module_name,
                        symbol_name=symbol_name,
                        qualname=qualname,
                        line_start=int(function["line_start"]),
                        line_end=int(function["line_end"]),
                        source=str(function["source"]),
                        imports=self._extract_imports(source),
                        decorators=[package_name],
                        is_public=symbol_name[:1].isupper(),
                        dependency_hints=sorted(set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", str(function["source"])))),
                        complexity_score=self._complexity_score(str(function["source"])),
                        io_hint=self._contains_io_hint(str(function["source"])),
                        target_score=self._score_function(symbol_name, str(function["source"])),
                        language=self.language_id,
                        adapter_id=self.adapter_id,
                        owner_qualname=function.get("owner_qualname"),
                        method_kind=str(function.get("method_kind", "function")),
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

    def _go_files(self) -> list[Path]:
        return sorted(
            path
            for path in self.project_root.rglob("*.go")
            if "vendor" not in path.parts
            and ".evotest" not in path.parts
            and "bin" not in path.parts
            and not path.name.endswith("_test.go")
        )

    @staticmethod
    def _read_text(file_path: Path) -> str | None:
        try:
            return file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def _module_path(self) -> str:
        go_mod = self.project_root / "go.mod"
        if not go_mod.exists():
            return self.project_root.name
        try:
            text = go_mod.read_text(encoding="utf-8")
        except OSError:
            return self.project_root.name
        match = re.search(r"(?m)^\s*module\s+(\S+)\s*$", text)
        return match.group(1) if match else self.project_root.name

    def _package_import_path(self, file_path: Path) -> str:
        relative_dir = file_path.relative_to(self.project_root).parent.as_posix()
        if relative_dir == ".":
            return self.module_path
        return f"{self.module_path}/{relative_dir}"

    @staticmethod
    def _package_name(source: str) -> str:
        match = re.search(r"(?m)^\s*package\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", source)
        return match.group(1) if match else "main"

    def _module_public_symbols(self, source: str) -> list[str]:
        symbols = set()
        for function in self._functions(source):
            symbol_name = str(function["symbol_name"])
            if symbol_name[:1].isupper():
                symbols.add(symbol_name)
        return sorted(symbols)

    def _functions(self, source: str) -> list[dict[str, object]]:
        functions: list[dict[str, object]] = []
        pattern = re.compile(
            r"(?m)^func\s+(?:\((?P<receiver>[^)]+)\)\s*)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
        )
        for match in pattern.finditer(source):
            receiver = match.group("receiver")
            name = match.group("name")
            snippet, line_start, line_end = self._extract_source_block(source, match.start())
            owner_qualname = self._receiver_type(receiver) if receiver else None
            qualname = f"{owner_qualname}.{name}" if owner_qualname else name
            functions.append(
                {
                    "qualname": qualname,
                    "symbol_name": name,
                    "line_start": line_start,
                    "line_end": line_end,
                    "source": snippet,
                    "owner_qualname": owner_qualname,
                    "method_kind": "instance" if owner_qualname else "function",
                }
            )
        return functions

    @staticmethod
    def _receiver_type(receiver: str) -> str | None:
        if not receiver:
            return None
        tokens = receiver.strip().split()
        if not tokens:
            return None
        type_token = tokens[-1].lstrip("*")
        type_token = type_token.split("[", 1)[0]
        return type_token or None

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
    def _extract_imports(source: str) -> list[ImportRef]:
        imports: list[ImportRef] = []
        for match in re.finditer(r'(?m)^\s*import\s+"([^"]+)"\s*$', source):
            imports.append(ImportRef(module=match.group(1), names=[match.group(1).rsplit("/", 1)[-1]], level=0))
        grouped = re.search(r"(?s)import\s*\((.*?)\)", source)
        if grouped:
            for raw_line in grouped.group(1).splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                match = re.match(r'(?:(?P<alias>[.\w_]+)\s+)?"(?P<path>[^"]+)"', line)
                if not match:
                    continue
                alias = match.group("alias") or match.group("path").rsplit("/", 1)[-1]
                imports.append(ImportRef(module=match.group("path"), names=[alias], level=0))
        return imports

    @staticmethod
    def _complexity_score(source: str) -> int:
        tokens = ("if ", "for ", "switch ", "select ", "case ", "&&", "||", " go ", " defer ")
        return sum(source.count(token) for token in tokens)

    @staticmethod
    def _contains_io_hint(source: str) -> bool:
        lowered = source.lower()
        return any(
            marker in lowered
            for marker in ("os.", "ioutil.", "filepath.", "http.", "net.", "exec.", "bufio.", "fs.")
        )

    def _score_function(self, name: str, source: str) -> float:
        score = 4.0 if name[:1].isupper() else 1.0
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
