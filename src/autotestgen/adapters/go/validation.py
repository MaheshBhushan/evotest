from __future__ import annotations

import re

from autotestgen.models import FunctionTarget, ModuleTarget


class GoModelOutputValidator:
    def validate(self, test_source: str, target: FunctionTarget | ModuleTarget) -> None:
        test_names = re.findall(r"(?m)^func\s+(Test[A-Za-z0-9_]+)\s*\(\s*t\s+\*testing\.T\s*\)", test_source)
        if not test_names:
            raise ValueError("Model output did not define any Go test functions.")
        if len(test_names) != len(set(test_names)):
            raise ValueError("Model output defined duplicate Go test names.")
        if self._contains_forbidden_side_effects(test_source):
            raise ValueError("Model output used forbidden network or process side effects.")
        if not self._references_target(test_source, target):
            raise ValueError("Model output did not reference the requested target.")
        if "t.Fatalf(" not in test_source and "t.Errorf(" not in test_source and "t.Fatal(" not in test_source:
            raise ValueError("Model output did not contain a Go test assertion.")

    @staticmethod
    def _contains_forbidden_side_effects(source: str) -> bool:
        lowered = source.lower()
        return any(
            marker in lowered
            for marker in (
                '"os/exec"',
                "exec.command",
                '"net/http"',
                "http.get(",
                "http.post(",
                "syscall.",
            )
        )

    @classmethod
    def _references_target(cls, source: str, target: FunctionTarget | ModuleTarget) -> bool:
        import_aliases, dot_imported = cls._target_import_aliases(source, target.module_name)
        package_name = target.module_name.rsplit("/", 1)[-1]
        if isinstance(target, ModuleTarget):
            return bool(import_aliases or dot_imported or cls._package_declares_target(source, package_name))
        if any(re.search(rf"\b{re.escape(alias)}\.{re.escape(target.symbol_name)}\s*\(", source) for alias in import_aliases):
            return True
        if dot_imported and re.search(rf"\b{re.escape(target.symbol_name)}\s*\(", source):
            return True
        if cls._package_declares_target(source, package_name):
            if target.is_method and target.owner_qualname:
                return cls._same_package_method_reference(source, target)
            return re.search(rf"\b{re.escape(target.symbol_name)}\s*\(", source) is not None
        if target.is_method and target.owner_qualname:
            return cls._imported_method_reference(source, target, import_aliases)
        return False

    @staticmethod
    def _target_import_aliases(source: str, target_module: str) -> tuple[set[str], bool]:
        aliases: set[str] = set()
        dot_imported = False
        pattern = re.compile(r'(?m)^\s*(?:(?P<alias>[.\w_]+)\s+)?"(?P<path>[^"]+)"\s*$')
        import_lines: list[str] = []
        block = re.search(r"(?s)import\s*\((.*?)\)", source)
        if block:
            import_lines.extend(line.strip() for line in block.group(1).splitlines() if line.strip())
        for match in re.finditer(r'(?m)^\s*import\s+(?:(?P<alias>[.\w_]+)\s+)?"(?P<path>[^"]+)"\s*$', source):
            import_lines.append(match.group(0).replace("import", "", 1).strip())
        for line in import_lines:
            match = pattern.match(line)
            if match is None or match.group("path") != target_module:
                continue
            alias = match.group("alias")
            if alias == ".":
                dot_imported = True
            else:
                aliases.add(alias or target_module.rsplit("/", 1)[-1])
        return aliases, dot_imported

    @staticmethod
    def _package_declares_target(source: str, package_name: str) -> bool:
        return re.search(rf"(?m)^\s*package\s+{re.escape(package_name)}(?:_test)?\s*$", source) is not None

    @classmethod
    def _imported_method_reference(
        cls,
        source: str,
        target: FunctionTarget,
        import_aliases: set[str],
    ) -> bool:
        owner = target.owner_qualname or ""
        for alias in import_aliases:
            if re.search(rf"\b{re.escape(alias)}\.{re.escape(owner)}\s*\{{", source):
                variable_names = cls._receiver_variables(source, rf"{re.escape(alias)}\.{re.escape(owner)}")
                if any(re.search(rf"\b{re.escape(name)}\.{re.escape(target.symbol_name)}\s*\(", source) for name in variable_names):
                    return True
                if re.search(
                    rf"\b{re.escape(alias)}\.{re.escape(owner)}\s*\{{[^}}]*\}}\.{re.escape(target.symbol_name)}\s*\(",
                    source,
                    re.DOTALL,
                ):
                    return True
        return False

    @classmethod
    def _same_package_method_reference(cls, source: str, target: FunctionTarget) -> bool:
        owner = target.owner_qualname or ""
        variable_names = cls._receiver_variables(source, re.escape(owner))
        if any(re.search(rf"\b{re.escape(name)}\.{re.escape(target.symbol_name)}\s*\(", source) for name in variable_names):
            return True
        return re.search(rf"\b{re.escape(owner)}\s*\{{[^}}]*\}}\.{re.escape(target.symbol_name)}\s*\(", source, re.DOTALL) is not None

    @staticmethod
    def _receiver_variables(source: str, receiver_type_pattern: str) -> set[str]:
        names: set[str] = set()
        patterns = (
            rf"\b([A-Za-z_][A-Za-z0-9_]*)\s*:=\s*&?{receiver_type_pattern}\s*\{{",
            rf"\bvar\s+([A-Za-z_][A-Za-z0-9_]*)\s+\*?{receiver_type_pattern}\b",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, source):
                names.add(match.group(1))
        return names
