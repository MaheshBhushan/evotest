from __future__ import annotations

from evotest.models import ExecutionResult, FailureClassification


class JavaScriptFailureClassifier:
    def classify(self, result: ExecutionResult) -> FailureClassification | None:
        if result.success:
            return None
        text = "\n".join(part for part in [result.stdout, result.stderr] if part)
        lowered = text.lower()
        if result.exit_code == 124:
            return self._failure(
                "execution_control",
                "timeout",
                True,
                "JavaScript test execution exceeded the configured timeout.",
                "Execution timed out before producing a stable result.",
            )
        checks: list[tuple[str, str, str, bool, str, str]] = [
            ("syntaxerror", "collection_parse", "syntax_error", True, "Syntax error while collecting generated tests.", "Generated test code did not parse."),
            ("referenceerror", "runtime", "name_error", True, "ReferenceError raised during execution.", "The generated test referenced an unknown symbol."),
            ("typeerror", "runtime", "type_error", True, "TypeError raised during execution.", "Inputs or mocks do not match the target's expected contract."),
            ("cannot find module", "import_dependency", "missing_dependency", False, "A required dependency was not available.", "Execution depends on an unavailable package."),
            ("failed to resolve import", "import_dependency", "import_error", True, "Import failed during JavaScript test execution.", "The generated test or target module could not be imported."),
            ("enoent", "environment_integration", "path_error", True, "A required path was not available during execution.", "The generated test referenced a path that does not exist."),
            ("permission", "environment_integration", "permission_error", False, "Permission denied during execution.", "The harness attempted an operation blocked by the environment."),
            ("expect(", "assertion_semantics", "assertion_failure", True, "Assertion failure in generated tests.", "The generated expectation does not match runtime behavior."),
        ]
        for needle, category, subtype, recoverable, message, summary in checks:
            if needle in lowered:
                return self._failure(category, subtype, recoverable, self._short_message(text, message), summary)
        if result.coverage and result.coverage.coverage_percent <= 0.0:
            return self._failure(
                "test_design",
                "no_coverage_gain",
                True,
                "Execution passed but produced no target coverage.",
                "The generated test did not exercise the target module.",
            )
        return self._failure(
            "runtime",
            "runtime_error",
            True,
            self._short_message(text, "Unhandled JavaScript runtime error."),
            "Execution failed with an uncategorized runtime error.",
        )

    @staticmethod
    def _failure(
        category: str,
        subtype: str,
        recoverable: bool,
        message: str,
        summary: str,
    ) -> FailureClassification:
        return FailureClassification(
            category=category,
            subtype=subtype,
            recoverable=recoverable,
            message=message,
            summary=summary,
        )

    @staticmethod
    def _short_message(text: str, fallback: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[-1][:240] if lines else fallback
