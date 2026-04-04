from __future__ import annotations

import ast
from dataclasses import dataclass

from evotest.models import FunctionTarget, ModuleTarget


@dataclass(slots=True)
class _CollectedTest:
    qualified_name: str
    node: ast.FunctionDef | ast.AsyncFunctionDef


class PythonModelOutputValidator:
    def validate(self, test_source: str, target: FunctionTarget | ModuleTarget) -> None:
        tree = ast.parse(test_source)
        collected_tests = self._collect_pytest_tests(tree)
        if not collected_tests:
            raise ValueError("Model output did not define any pytest test functions.")
        names = [item.qualified_name for item in collected_tests]
        if len(set(names)) != len(names):
            raise ValueError("Model output defined duplicate pytest test names.")
        if self._contains_forbidden_side_effects(tree):
            raise ValueError("Model output used forbidden network or process side effects.")
        if not self._references_target(tree, target):
            raise ValueError("Model output did not reference the requested target.")
        for collected_test in collected_tests:
            assertions = [node for node in ast.walk(collected_test.node) if isinstance(node, ast.Assert)]
            if not assertions:
                raise ValueError(f"Model output test {collected_test.qualified_name} did not contain an assertion.")
            if all(self._is_weak_assertion(assertion) for assertion in assertions):
                raise ValueError(f"Model output test {collected_test.qualified_name} only contained weak assertions.")

    @staticmethod
    def _collect_pytest_tests(tree: ast.Module) -> list[_CollectedTest]:
        collected: list[_CollectedTest] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
                collected.append(_CollectedTest(qualified_name=node.name, node=node))
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name.startswith("test_"):
                        collected.append(_CollectedTest(qualified_name=f"{node.name}.{member.name}", node=member))
        return collected

    @staticmethod
    def _contains_forbidden_side_effects(tree: ast.AST) -> bool:
        forbidden_import_roots = {"requests", "httpx", "aiohttp", "socket", "subprocess", "urllib", "websocket"}
        forbidden_calls = {
            "requests.get",
            "requests.post",
            "httpx.get",
            "httpx.post",
            "socket.socket",
            "socket.create_connection",
            "urllib.request.urlopen",
            "subprocess.run",
            "subprocess.Popen",
            "os.system",
        }
        module_aliases, symbol_aliases = PythonModelOutputValidator._import_bindings(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] in forbidden_import_roots:
                        return True
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".", 1)[0] in forbidden_import_roots:
                    return True
            if isinstance(node, ast.Call):
                dotted = PythonModelOutputValidator._resolve_imported_name(
                    PythonModelOutputValidator._dotted_name(node.func),
                    module_aliases=module_aliases,
                    symbol_aliases=symbol_aliases,
                )
                if dotted in forbidden_calls:
                    return True
        return False

    @staticmethod
    def _references_target(tree: ast.AST, target: FunctionTarget | ModuleTarget) -> bool:
        module_aliases, symbol_aliases = PythonModelOutputValidator._target_aliases(tree, target)
        import_module_aliases, import_symbol_aliases = PythonModelOutputValidator._import_bindings(tree)
        raw_call_names = {
            dotted
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for dotted in [PythonModelOutputValidator._dotted_name(node.func)]
            if dotted is not None
        }
        resolved_call_names = {
            dotted
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for dotted in [
                PythonModelOutputValidator._resolve_imported_name(
                    PythonModelOutputValidator._dotted_name(node.func),
                    module_aliases=import_module_aliases,
                    symbol_aliases=import_symbol_aliases,
                )
            ]
            if dotted is not None
        }
        if isinstance(target, FunctionTarget):
            return PythonModelOutputValidator._calls_function_target(
                target,
                tree=tree,
                raw_call_names=raw_call_names,
                resolved_call_names=resolved_call_names,
                module_aliases=module_aliases,
                symbol_aliases=symbol_aliases,
                import_module_aliases=import_module_aliases,
                import_symbol_aliases=import_symbol_aliases,
            )
        target_names = set(target.module_name.split("."))
        target_names.add(target.module_name.split(".")[-1])
        target_names.update(target.public_symbols)
        seen_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        seen_attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        if target_names.intersection(seen_names | seen_attrs):
            return True
        if symbol_aliases.intersection(seen_names):
            return True
        if module_aliases.intersection(seen_names):
            return True
        for dotted_name in raw_call_names:
            if any(dotted_name.startswith(f"{alias}.") for alias in module_aliases | symbol_aliases):
                return True
        return False

    @staticmethod
    def _calls_function_target(
        target: FunctionTarget,
        *,
        tree: ast.AST,
        raw_call_names: set[str],
        resolved_call_names: set[str],
        module_aliases: set[str],
        symbol_aliases: set[str],
        import_module_aliases: dict[str, str],
        import_symbol_aliases: dict[str, str],
    ) -> bool:
        if target.is_method:
            return PythonModelOutputValidator._calls_method_target(
                tree,
                target,
                module_aliases=import_module_aliases,
                symbol_aliases=import_symbol_aliases,
            )
        if target.symbol_name in raw_call_names or symbol_aliases.intersection(raw_call_names):
            return True
        if any(call == f"{alias}.{target.symbol_name}" for call in raw_call_names for alias in module_aliases):
            return True
        return f"{target.module_name}.{target.symbol_name}" in resolved_call_names

    @staticmethod
    def _calls_method_target(
        tree: ast.AST,
        target: FunctionTarget,
        *,
        module_aliases: dict[str, str],
        symbol_aliases: dict[str, str],
    ) -> bool:
        if not isinstance(tree, ast.Module) or not target.owner_qualname:
            return False
        owner_path = f"{target.module_name}.{target.owner_qualname}"
        method_path = f"{owner_path}.{target.symbol_name}"
        for collected_test in PythonModelOutputValidator._collect_pytest_tests(tree):
            owner_instance_names = PythonModelOutputValidator._owner_instance_names(
                collected_test.node,
                owner_path=owner_path,
                module_aliases=module_aliases,
                symbol_aliases=symbol_aliases,
            )
            for node in ast.walk(collected_test.node):
                if not isinstance(node, ast.Call):
                    continue
                call_name = PythonModelOutputValidator._resolve_imported_name(
                    PythonModelOutputValidator._dotted_name(node.func),
                    module_aliases=module_aliases,
                    symbol_aliases=symbol_aliases,
                )
                if call_name == method_path:
                    return True
                if not isinstance(node.func, ast.Attribute) or node.func.attr != target.symbol_name:
                    continue
                owner_name = PythonModelOutputValidator._resolve_imported_name(
                    PythonModelOutputValidator._dotted_name(node.func.value),
                    module_aliases=module_aliases,
                    symbol_aliases=symbol_aliases,
                )
                if owner_name == owner_path:
                    return True
                if isinstance(node.func.value, ast.Name) and node.func.value.id in owner_instance_names:
                    return True
                if isinstance(node.func.value, ast.Call):
                    constructor_name = PythonModelOutputValidator._resolve_imported_name(
                        PythonModelOutputValidator._dotted_name(node.func.value.func),
                        module_aliases=module_aliases,
                        symbol_aliases=symbol_aliases,
                    )
                    if constructor_name == owner_path:
                        return True
        return False

    @staticmethod
    def _owner_instance_names(
        tree: ast.AST,
        *,
        owner_path: str,
        module_aliases: dict[str, str],
        symbol_aliases: dict[str, str],
    ) -> set[str]:
        owner_instances: set[str] = set()
        changed = True
        while changed:
            changed = False
            for node in ast.walk(tree):
                value: ast.AST | None = None
                target_nodes: list[ast.AST] = []
                if isinstance(node, ast.Assign):
                    value = node.value
                    target_nodes = list(node.targets)
                elif isinstance(node, ast.AnnAssign):
                    value = node.value
                    target_nodes = [node.target]
                elif isinstance(node, ast.NamedExpr):
                    value = node.value
                    target_nodes = [node.target]
                if value is None or not PythonModelOutputValidator._is_owner_instance_expression(
                    value,
                    owner_path=owner_path,
                    owner_instances=owner_instances,
                    module_aliases=module_aliases,
                    symbol_aliases=symbol_aliases,
                ):
                    continue
                for target_name in PythonModelOutputValidator._assignment_target_names(target_nodes):
                    if target_name not in owner_instances:
                        owner_instances.add(target_name)
                        changed = True
        return owner_instances

    @staticmethod
    def _is_owner_instance_expression(
        node: ast.AST,
        *,
        owner_path: str,
        owner_instances: set[str],
        module_aliases: dict[str, str],
        symbol_aliases: dict[str, str],
    ) -> bool:
        if isinstance(node, ast.Call):
            constructor_name = PythonModelOutputValidator._resolve_imported_name(
                PythonModelOutputValidator._dotted_name(node.func),
                module_aliases=module_aliases,
                symbol_aliases=symbol_aliases,
            )
            return constructor_name == owner_path
        if isinstance(node, ast.Name):
            return node.id in owner_instances
        return False

    @staticmethod
    def _assignment_target_names(targets: list[ast.AST]) -> set[str]:
        names: set[str] = set()
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, (ast.Tuple, ast.List)):
                names.update(PythonModelOutputValidator._assignment_target_names(list(target.elts)))
        return names

    @staticmethod
    def _target_aliases(
        tree: ast.AST,
        target: FunctionTarget | ModuleTarget,
    ) -> tuple[set[str], set[str]]:
        module_aliases: set[str] = set()
        symbol_aliases: set[str] = set()
        module_name = target.module_name
        function_parts = target.qualname.split(".") if isinstance(target, FunctionTarget) else []
        symbol_candidates = set(function_parts)
        if isinstance(target, FunctionTarget):
            symbol_candidates.add(target.symbol_name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == module_name:
                        module_aliases.add(alias.asname or alias.name.split(".")[-1])
            elif isinstance(node, ast.ImportFrom) and node.module == module_name:
                for alias in node.names:
                    if isinstance(target, ModuleTarget):
                        module_aliases.add(alias.asname or alias.name)
                    elif alias.name in symbol_candidates:
                        symbol_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module and module_name.startswith(f"{node.module}."):
                for alias in node.names:
                    if alias.name == module_name.split(".")[-1]:
                        module_aliases.add(alias.asname or alias.name)
        return module_aliases, symbol_aliases

    @staticmethod
    def _import_bindings(tree: ast.AST) -> tuple[dict[str, str], dict[str, str]]:
        module_aliases: dict[str, str] = {}
        symbol_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_aliases[alias.asname or alias.name.split(".")[-1]] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    symbol_aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        return module_aliases, symbol_aliases

    @staticmethod
    def _resolve_imported_name(
        dotted_name: str | None,
        *,
        module_aliases: dict[str, str],
        symbol_aliases: dict[str, str],
    ) -> str | None:
        if dotted_name is None:
            return None
        parts = dotted_name.split(".")
        head = parts[0]
        if head in symbol_aliases:
            return ".".join([symbol_aliases[head], *parts[1:]])
        if head in module_aliases:
            return ".".join([module_aliases[head], *parts[1:]])
        return dotted_name

    @staticmethod
    def _is_weak_assertion(node: ast.Assert) -> bool:
        test = node.test
        if isinstance(test, ast.Constant) and isinstance(test.value, bool):
            return True
        if isinstance(test, ast.Name):
            return True
        if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or) and len(test.values) == 2:
            left, right = test.values
            if (
                PythonModelOutputValidator._is_none_check(left, positive=False)
                and PythonModelOutputValidator._is_none_check(right, positive=True)
                and PythonModelOutputValidator._same_none_target(left, right)
            ):
                return True
            if (
                PythonModelOutputValidator._is_none_check(left, positive=True)
                and PythonModelOutputValidator._is_none_check(right, positive=False)
                and PythonModelOutputValidator._same_none_target(left, right)
            ):
                return True
        if PythonModelOutputValidator._is_none_check(test, positive=False):
            return True
        return False

    @staticmethod
    def _is_none_check(node: ast.AST, *, positive: bool) -> bool:
        if not isinstance(node, ast.Compare):
            return False
        if len(node.ops) != 1 or len(node.comparators) != 1:
            return False
        comparator = node.comparators[0]
        if not (isinstance(comparator, ast.Constant) and comparator.value is None):
            return False
        operator = node.ops[0]
        return isinstance(operator, ast.Is if positive else ast.IsNot)

    @staticmethod
    def _same_none_target(left: ast.AST, right: ast.AST) -> bool:
        if not isinstance(left, ast.Compare) or not isinstance(right, ast.Compare):
            return False
        return ast.dump(left.left) == ast.dump(right.left)

    @staticmethod
    def _dotted_name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = PythonModelOutputValidator._dotted_name(node.value)
            if base is None:
                return None
            return f"{base}.{node.attr}"
        return None
