from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from evotest.models import FunctionTarget, ImportRef, ModuleTarget, ProjectInventory


@dataclass(slots=True)
class _ParsedModule:
    tree: ast.Module
    source: str
    module_name: str
    imports: list[ImportRef]


@dataclass(slots=True)
class _ClassContext:
    qualname: str
    constructor_parameters: list[str]


class PythonProjectAnalyzer:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.import_roots = self._discover_import_roots()

    def discover(self) -> ProjectInventory:
        functions: list[FunctionTarget] = []
        modules: list[ModuleTarget] = []
        for file_path in self._python_files():
            parsed = self._parse_module(file_path)
            if parsed is None:
                continue
            module_target = self._module_target(file_path, parsed)
            modules.append(module_target)
            functions.extend(self._function_targets(file_path, parsed))
        functions.sort(key=lambda target: (-target.target_score, target.target_id))
        modules.sort(key=lambda target: (-target.target_score, target.target_id))
        return ProjectInventory(functions=functions, modules=modules)

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

    def _python_files(self) -> list[Path]:
        return sorted(
            path
            for path in self.project_root.rglob("*.py")
            if ".venv" not in path.parts
            and "__pycache__" not in path.parts
            and ".evotest" not in path.parts
            and "tests" not in path.parts
            and not path.name.startswith("test_")
        )

    def _parse_module(self, file_path: Path) -> _ParsedModule | None:
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, UnicodeDecodeError):
            return None
        imports = self._extract_imports(tree)
        return _ParsedModule(
            tree=tree,
            source=source,
            module_name=self._module_name_for(file_path),
            imports=imports,
        )

    def _module_target(self, file_path: Path, parsed: _ParsedModule) -> ModuleTarget:
        relative_path = file_path.relative_to(self.project_root).as_posix()
        public_symbols = []
        for node in parsed.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                public_symbols.append(node.name)
        complexity_score = self._complexity_score(parsed.tree)
        io_hint = self._contains_io_hint(parsed.tree)
        return ModuleTarget(
            target_id=relative_path,
            file_path=file_path,
            module_name=parsed.module_name,
            source=parsed.source,
            imports=parsed.imports,
            public_symbols=public_symbols,
            complexity_score=complexity_score,
            io_hint=io_hint,
            target_score=self._score_module(public_symbols, parsed.imports, complexity_score, io_hint),
        )

    def _function_targets(self, file_path: Path, parsed: _ParsedModule) -> list[FunctionTarget]:
        functions: list[FunctionTarget] = []
        parents: list[str] = []
        class_stack: list[_ClassContext] = []
        function_depth = 0
        relative_path = file_path.relative_to(self.project_root).as_posix()
        analyzer = self

        class Visitor(ast.NodeVisitor):
            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                class_qualname = ".".join([*parents, node.name])
                class_stack.append(
                    _ClassContext(
                        qualname=class_qualname,
                        constructor_parameters=analyzer._constructor_parameters(node),
                    )
                )
                parents.append(node.name)
                self.generic_visit(node)
                parents.pop()
                class_stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                nonlocal function_depth
                if function_depth == 0:
                    functions.append(
                        self._build_function(node, is_direct_class_member=bool(class_stack) and function_depth == 0)
                    )
                function_depth += 1
                parents.append(node.name)
                self.generic_visit(node)
                parents.pop()
                function_depth -= 1

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                nonlocal function_depth
                if function_depth == 0:
                    functions.append(
                        self._build_function(node, is_direct_class_member=bool(class_stack) and function_depth == 0)
                    )
                function_depth += 1
                parents.append(node.name)
                self.generic_visit(node)
                parents.pop()
                function_depth -= 1

            def _build_function(
                self,
                node: ast.FunctionDef | ast.AsyncFunctionDef,
                *,
                is_direct_class_member: bool,
            ) -> FunctionTarget:
                qualname_parts = [*parents, node.name]
                qualname = ".".join(qualname_parts)
                source = ast.get_source_segment(parsed.source, node) or ""
                dependency_hints = sorted({subnode.id for subnode in ast.walk(node) if isinstance(subnode, ast.Name)})
                decorators = [ast.unparse(decorator) for decorator in node.decorator_list]
                target_id = f"{relative_path}::{qualname}"
                complexity_score = analyzer._complexity_score(node)
                io_hint = analyzer._contains_io_hint(node)
                owner_qualname = class_stack[-1].qualname if is_direct_class_member else None
                constructor_parameters = list(class_stack[-1].constructor_parameters) if is_direct_class_member else []
                method_kind = analyzer._method_kind_for(node, is_direct_class_member)
                return FunctionTarget(
                    target_id=target_id,
                    file_path=file_path,
                    module_name=parsed.module_name,
                    symbol_name=node.name,
                    qualname=qualname,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    source=source,
                    imports=parsed.imports,
                    decorators=decorators,
                    is_public=not node.name.startswith("_"),
                    dependency_hints=dependency_hints,
                    complexity_score=complexity_score,
                    io_hint=io_hint,
                    target_score=analyzer._score_function(
                        is_public=not node.name.startswith("_"),
                        dependency_count=len(dependency_hints),
                        decorator_count=len(decorators),
                        complexity_score=complexity_score,
                        io_hint=io_hint,
                    ),
                    owner_qualname=owner_qualname,
                    method_kind=method_kind,
                    constructor_parameters=constructor_parameters,
                )

        Visitor().visit(parsed.tree)
        return functions

    @staticmethod
    def _constructor_parameters(node: ast.ClassDef) -> list[str]:
        for member in node.body:
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name == "__init__":
                return [arg.arg for arg in member.args.args if arg.arg not in {"self", "cls"}]
        return []

    @staticmethod
    def _method_kind_for(node: ast.FunctionDef | ast.AsyncFunctionDef, is_direct_class_member: bool) -> str:
        if not is_direct_class_member:
            return "function"
        decorator_names = {ast.unparse(decorator).split(".")[-1] for decorator in node.decorator_list}
        if "staticmethod" in decorator_names:
            return "static"
        if "classmethod" in decorator_names:
            return "class"
        return "instance"

    @staticmethod
    def _extract_imports(tree: ast.Module) -> list[ImportRef]:
        imports: list[ImportRef] = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(ImportRef(module=alias.name, names=[alias.asname or alias.name], level=0))
            elif isinstance(node, ast.ImportFrom):
                imports.append(
                    ImportRef(
                        module=node.module,
                        names=[alias.asname or alias.name for alias in node.names],
                        level=node.level,
                    )
                )
        return imports

    def _module_name_for(self, file_path: Path) -> str:
        root = self._best_import_root(file_path)
        relative = file_path.relative_to(root)
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts)

    def _discover_import_roots(self) -> list[Path]:
        roots = []
        src_root = self.project_root / "src"
        if src_root.exists():
            roots.append(src_root)
        roots.append(self.project_root)
        return roots

    def _best_import_root(self, file_path: Path) -> Path:
        matching_roots = [root for root in self.import_roots if self._is_relative_to(file_path, root)]
        if not matching_roots:
            return self.project_root
        return min(matching_roots, key=lambda root: len(file_path.relative_to(root).parts))

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True

    def _complexity_score(self, node: ast.AST) -> int:
        branch_types = (
            ast.If,
            ast.For,
            ast.AsyncFor,
            ast.While,
            ast.Try,
            ast.With,
            ast.AsyncWith,
            ast.Match,
            ast.BoolOp,
            ast.IfExp,
            ast.comprehension,
        )
        return sum(1 for subnode in ast.walk(node) if isinstance(subnode, branch_types))

    def _contains_io_hint(self, node: ast.AST) -> bool:
        io_names = {
            "open",
            "read",
            "write",
            "requests",
            "socket",
            "subprocess",
            "Path",
            "unlink",
            "mkdir",
            "rmdir",
            "remove",
            "rename",
        }
        names = {subnode.id for subnode in ast.walk(node) if isinstance(subnode, ast.Name)}
        attrs = {subnode.attr for subnode in ast.walk(node) if isinstance(subnode, ast.Attribute)}
        return bool(io_names.intersection(names | attrs))

    @staticmethod
    def _score_function(
        *,
        is_public: bool,
        dependency_count: int,
        decorator_count: int,
        complexity_score: int,
        io_hint: bool,
    ) -> float:
        score = 0.0
        score += 4.0 if is_public else 1.0
        score += min(dependency_count, 12) * 0.25
        score += decorator_count * 0.4
        score += min(complexity_score, 15) * 0.5
        if io_hint:
            score += 1.5
        return round(score, 2)

    @staticmethod
    def _score_module(
        public_symbols: Iterable[str],
        imports: list[ImportRef],
        complexity_score: int,
        io_hint: bool,
    ) -> float:
        score = float(len(list(public_symbols))) * 0.75
        score += min(len(imports), 20) * 0.15
        score += min(complexity_score, 20) * 0.35
        if io_hint:
            score += 1.0
        return round(score, 2)
