from __future__ import annotations

import re

from autotestgen.models import FunctionTarget, ModuleTarget


class JavaScriptModelOutputValidator:
    def validate(self, test_source: str, target: FunctionTarget | ModuleTarget) -> None:
        self._validate_basic_syntax(test_source)
        if not re.search(r"\b(?:test|it)\s*\(", test_source):
            raise ValueError("Model output did not define any JavaScript test cases.")
        names = re.findall(r"\b(?:test|it)\s*\(\s*['\"]([^'\"]+)['\"]", test_source)
        if len(names) != len(set(names)):
            raise ValueError("Model output defined duplicate JavaScript test names.")
        if self._contains_forbidden_side_effects(test_source):
            raise ValueError("Model output used forbidden network or process side effects.")
        if not self._references_target(test_source, target):
            raise ValueError("Model output did not reference the requested target.")
        if not re.search(r"\bexpect\s*\(", test_source):
            raise ValueError("Model output did not contain an assertion.")
        if self._only_weak_assertions(test_source):
            raise ValueError("Model output only contained weak assertions.")

    @staticmethod
    def _contains_forbidden_side_effects(source: str) -> bool:
        lowered = source.lower()
        return any(
            marker in lowered
            for marker in (
                "fetch(",
                "axios.",
                "http.",
                "https.",
                "child_process",
                "exec(",
                "spawn(",
                "process.exit(",
                "xmlhttprequest",
            )
        )

    @staticmethod
    def _validate_basic_syntax(source: str) -> None:
        stack: list[str] = []
        string_quote: str | None = None
        in_line_comment = False
        in_block_comment = False
        escape = False
        pairs = {"(": ")", "[": "]", "{": "}"}
        reverse_pairs = {value: key for key, value in pairs.items()}
        index = 0
        while index < len(source):
            char = source[index]
            next_char = source[index + 1] if index + 1 < len(source) else ""
            if in_line_comment:
                if char == "\n":
                    in_line_comment = False
                index += 1
                continue
            if in_block_comment:
                if char == "*" and next_char == "/":
                    in_block_comment = False
                    index += 2
                    continue
                index += 1
                continue
            if string_quote is not None:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == string_quote:
                    string_quote = None
                index += 1
                continue
            if char == "/" and next_char == "/":
                in_line_comment = True
                index += 2
                continue
            if char == "/" and next_char == "*":
                in_block_comment = True
                index += 2
                continue
            if char in {"'", '"', "`"}:
                string_quote = char
                index += 1
                continue
            if char in pairs:
                stack.append(char)
                index += 1
                continue
            if char in reverse_pairs:
                if not stack or stack[-1] != reverse_pairs[char]:
                    raise ValueError("Model output did not parse as JavaScript.")
                stack.pop()
            index += 1
        if string_quote is not None or in_block_comment or stack:
            raise ValueError("Model output did not parse as JavaScript.")

    @classmethod
    def _references_target(cls, source: str, target: FunctionTarget | ModuleTarget) -> bool:
        callable_aliases, namespace_aliases = cls._target_aliases(source, target)
        if isinstance(target, ModuleTarget):
            return bool(callable_aliases or namespace_aliases)
        if any(re.search(rf"\b{re.escape(alias)}\s*\(", source) for alias in callable_aliases):
            return True
        return any(
            re.search(rf"\b{re.escape(alias)}\.{re.escape(target.symbol_name)}\s*\(", source)
            for alias in namespace_aliases
        )

    @classmethod
    def _target_aliases(
        cls,
        source: str,
        target: FunctionTarget | ModuleTarget,
    ) -> tuple[set[str], set[str]]:
        callable_aliases: set[str] = set()
        namespace_aliases: set[str] = set()
        module_url_aliases: set[str] = set()
        target_name = target.symbol_name if isinstance(target, FunctionTarget) else None

        for match in re.finditer(
            r"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*pathToFileURL\([^)]*['\"]([^'\"]+)['\"][^)]*\)\.href",
            source,
        ):
            alias, specifier = match.groups()
            if cls._matches_target_module(specifier, target):
                module_url_aliases.add(alias)

        for match in re.finditer(
            r"\bimport\s*\{\s*([^}]+)\s*\}\s*from\s*['\"]([^'\"]+)['\"]",
            source,
        ):
            bindings, specifier = match.groups()
            if not cls._matches_target_module(specifier, target):
                continue
            if target_name is None:
                callable_aliases.update(cls._all_binding_aliases(bindings))
            else:
                callable_aliases.update(cls._binding_aliases(bindings, target_name))

        for match in re.finditer(
            r"\b(?:const|let|var)\s*\{\s*([^}]+)\s*\}\s*=\s*(?:await\s+)?(?:import|require)\(\s*['\"]([^'\"]+)['\"]\s*\)",
            source,
        ):
            bindings, specifier = match.groups()
            if not cls._matches_target_module(specifier, target):
                continue
            if target_name is None:
                callable_aliases.update(cls._all_binding_aliases(bindings))
            else:
                callable_aliases.update(cls._binding_aliases(bindings, target_name))

        for match in re.finditer(
            r"\bimport\s+\*\s+as\s+([A-Za-z_][A-Za-z0-9_]*)\s+from\s*['\"]([^'\"]+)['\"]",
            source,
        ):
            alias, specifier = match.groups()
            if cls._matches_target_module(specifier, target):
                namespace_aliases.add(alias)

        for match in re.finditer(
            r"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:await\s+)?(?:import|require)\(\s*(?:['\"]([^'\"]+)['\"]|([A-Za-z_][A-Za-z0-9_]*))\s*\)",
            source,
        ):
            alias, literal_specifier, variable_specifier = match.groups()
            if literal_specifier and cls._matches_target_module(literal_specifier, target):
                namespace_aliases.add(alias)
            elif variable_specifier and variable_specifier in module_url_aliases:
                namespace_aliases.add(alias)

        if isinstance(target, FunctionTarget):
            for namespace_alias in list(namespace_aliases):
                pattern = rf"\b(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*{re.escape(namespace_alias)}\.{re.escape(target.symbol_name)}\b"
                for match in re.finditer(pattern, source):
                    callable_aliases.add(match.group(1))

        return callable_aliases, namespace_aliases

    @staticmethod
    def _binding_aliases(bindings: str, target_name: str) -> set[str]:
        aliases: set[str] = set()
        for raw_binding in bindings.split(","):
            binding = raw_binding.strip()
            if not binding:
                continue
            if " as " in binding:
                original, alias = [part.strip() for part in binding.split(" as ", 1)]
            elif ":" in binding:
                original, alias = [part.strip() for part in binding.split(":", 1)]
            else:
                original = alias = binding
            if original == target_name:
                aliases.add(alias)
        return aliases

    @staticmethod
    def _all_binding_aliases(bindings: str) -> set[str]:
        aliases: set[str] = set()
        for raw_binding in bindings.split(","):
            binding = raw_binding.strip()
            if not binding:
                continue
            if " as " in binding:
                _, alias = [part.strip() for part in binding.split(" as ", 1)]
            elif ":" in binding:
                _, alias = [part.strip() for part in binding.split(":", 1)]
            else:
                alias = binding
            aliases.add(alias)
        return aliases

    @classmethod
    def _matches_target_module(cls, specifier: str, target: FunctionTarget | ModuleTarget) -> bool:
        normalized = specifier.replace("\\", "/").rstrip("/")
        exact_candidates = cls._exact_module_specifier_candidates(target)
        if normalized in exact_candidates:
            return True
        if not cls._is_relative_or_absolute_specifier(normalized):
            return False
        return any(
            normalized == candidate or normalized.endswith(f"/{candidate.lstrip('./')}")
            for candidate in cls._path_suffix_candidates(target)
        )

    @staticmethod
    def _exact_module_specifier_candidates(target: FunctionTarget | ModuleTarget) -> set[str]:
        relative_path = target.target_id.split("::", 1)[0].replace("\\", "/")
        relative_no_ext = relative_path.rsplit(".", 1)[0] if "." in relative_path else relative_path
        candidates = {relative_path}
        if "/" in relative_no_ext:
            candidates.add(relative_no_ext)
        return {candidate for candidate in candidates if candidate}

    @staticmethod
    def _path_suffix_candidates(target: FunctionTarget | ModuleTarget) -> set[str]:
        relative_path = target.target_id.split("::", 1)[0].replace("\\", "/")
        relative_no_ext = relative_path.rsplit(".", 1)[0] if "." in relative_path else relative_path
        file_name = target.file_path.name
        stem = target.file_path.stem
        candidates = {
            relative_path,
            relative_no_ext,
            file_name,
            stem,
        }
        if isinstance(target, ModuleTarget):
            candidates.update(target.public_symbols)
        return {candidate for candidate in candidates if candidate}

    @staticmethod
    def _is_relative_or_absolute_specifier(specifier: str) -> bool:
        return (
            specifier.startswith(".")
            or specifier.startswith("/")
            or specifier.startswith("file:")
            or bool(re.match(r"^[A-Za-z]:[\\/]", specifier))
        )

    @staticmethod
    def _only_weak_assertions(source: str) -> bool:
        assertion_lines = re.findall(r"expect\s*\((.*?)\)\.(.*?);", source, re.DOTALL)
        if not assertion_lines:
            return False
        strong_matchers = ("toBe(", "toEqual(", "toStrictEqual(", "toContain(", "toThrow(", "toMatch(")
        return all(not any(matcher in suffix for matcher in strong_matchers) for _, suffix in assertion_lines)
