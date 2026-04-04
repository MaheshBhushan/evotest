from __future__ import annotations

from evotest.models import ExecutionResult, FailureClassification


class GoFailureClassifier:
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
                "Go test execution exceeded the configured timeout.",
                "Execution timed out before producing a stable result.",
            )
        checks: list[tuple[str, str, str, bool, str, str]] = [
            (
                "go executable not found",
                "environment_integration",
                "missing_dependency",
                False,
                "Go executable was not available for test execution.",
                "The Go toolchain is not installed or not configured for the harness.",
            ),
            ("syntax error", "collection_parse", "syntax_error", True, "Syntax error while compiling generated tests.", "Generated Go test code did not parse."),
            ("undefined:", "runtime", "name_error", True, "An undefined symbol was referenced during execution.", "The generated test referenced an unknown symbol."),
            ("cannot use", "runtime", "type_error", True, "Type mismatch during execution.", "Generated inputs or assertions do not match the target's type contract."),
            ("panic:", "runtime", "runtime_error", True, "A panic occurred during execution.", "The generated path triggered a runtime panic."),
            ("no required module provides package", "import_dependency", "missing_dependency", False, "A required Go module was not available.", "Execution depends on an unavailable module."),
            ("build failed", "import_dependency", "import_error", True, "Go build failed during test execution.", "The generated test or target package could not be imported or compiled."),
            ("no such file or directory", "environment_integration", "path_error", True, "A required path was not available during execution.", "The generated test referenced a path that does not exist."),
            ("permission denied", "environment_integration", "permission_error", False, "Permission denied during execution.", "The harness attempted an operation blocked by the environment."),
            ("--- fail:", "assertion_semantics", "assertion_failure", True, "Assertion failure in generated tests.", "The generated expectation does not match runtime behavior."),
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
                "The generated test did not exercise the target package.",
            )
        return self._failure(
            "runtime",
            "runtime_error",
            True,
            self._short_message(text, "Unhandled Go runtime error."),
            "Execution failed with an uncategorized Go runtime error.",
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
