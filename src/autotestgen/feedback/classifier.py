from __future__ import annotations

from autotestgen.models import ExecutionResult, FailureClassification


class FailureClassifier:
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
                "Pytest execution exceeded the configured timeout.",
                "Execution timed out before producing a stable result.",
            )
        checks: list[tuple[str, str, str, bool, str, str]] = [
            ("syntaxerror", "collection_parse", "syntax_error", True, "SyntaxError while collecting generated tests.", "Generated test code did not parse."),
            ("indentationerror", "collection_parse", "indentation_error", True, "IndentationError while collecting generated tests.", "Generated test indentation is invalid."),
            ("collected 0 items", "collection_parse", "collection_error", True, "Pytest could not collect any generated tests.", "Generated tests were not collected."),
            ("modulenotfounderror", "import_dependency", "missing_dependency", False, "Missing dependency while importing the target.", "Execution depends on an unavailable module."),
            ("importerror", "import_dependency", "import_error", True, "Import failed during test execution.", "The generated test or target module could not be imported."),
            ("cannot import name", "import_dependency", "circular_import", True, "Import chain appears circular or incomplete.", "Import graph likely contains a circular dependency."),
            ("attributeerror", "runtime", "attribute_error", True, "Attribute error raised during execution.", "A mock or target object is missing expected API surface."),
            ("typeerror", "runtime", "type_error", True, "Type error raised during execution.", "Inputs or mocks do not match the target's expected contract."),
            ("valueerror", "runtime", "value_error", True, "Value error raised during execution.", "Generated inputs violate a value constraint."),
            ("keyerror", "runtime", "key_error", True, "Key error raised during execution.", "Generated inputs missed a required mapping key."),
            ("indexerror", "runtime", "index_error", True, "Index error raised during execution.", "Generated inputs hit an invalid positional access."),
            ("stopiteration", "runtime", "stop_iteration", True, "StopIteration raised during execution.", "The test exhausted an iterator unexpectedly."),
            ("nameerror", "runtime", "name_error", True, "NameError raised during execution.", "The target or generated test referenced an unknown symbol."),
            ("recursionerror", "runtime", "recursion_error", True, "RecursionError raised during execution.", "The generated path triggered uncontrolled recursion."),
            ("permissionerror", "environment_integration", "permission_error", False, "Permission denied during test execution.", "The harness attempted an operation blocked by the environment."),
            ("filenotfounderror", "environment_integration", "path_error", True, "A required path was not available during execution.", "The generated test referenced a path that does not exist."),
            ("unicode", "environment_integration", "encoding_error", False, "Encoding-related failure during execution.", "Execution failed because text encoding assumptions were invalid."),
            ("connectionerror", "environment_integration", "network_error", False, "Network access failed during execution.", "The test path depends on network access."),
            ("http", "environment_integration", "external_service_error", False, "External service interaction failed during execution.", "The target behavior depends on an unavailable external service."),
            ("fixture", "test_design", "bad_fixture", True, "Fixture setup failed for the generated test.", "The generated fixture or setup did not match the target's needs."),
            ("parametrize", "test_design", "bad_parametrization", True, "Generated parametrization was invalid.", "The test parameter matrix needs repair."),
            ("mock", "import_dependency", "mock_misconfiguration", True, "Generated mock configuration failed.", "Mocks were configured with the wrong surface or assumptions."),
            ("did not raise", "assertion_semantics", "exception_not_raised", True, "The expected exception was not raised.", "The generated exception expectation was too strict."),
            ("wrong exception", "assertion_semantics", "wrong_exception_type", True, "The generated test asserted the wrong exception type.", "Exception expectations need repair."),
            ("approx", "assertion_semantics", "float_tolerance_failure", True, "Floating-point comparison failed.", "The assertion needs a tolerance-aware comparison."),
            ("assert", "assertion_semantics", "assertion_failure", True, "Assertion failure in generated tests.", "The generated expectation does not match runtime behavior."),
        ]
        for needle, category, subtype, recoverable, fallback_message, summary in checks:
            if needle in lowered:
                return self._failure(
                    category,
                    subtype,
                    recoverable,
                    self._short_message(text, fallback_message),
                    summary,
                )
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
            self._short_message(text, "Unhandled runtime error."),
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
