from __future__ import annotations

from autotestgen.feedback.classifier import FailureClassifier
from autotestgen.models import ExecutionResult, FailureClassification


class PythonFailureClassifier:
    language_id = "python"
    adapter_id = "python"

    def __init__(self) -> None:
        self.classifier = FailureClassifier()

    def classify(self, result: ExecutionResult) -> FailureClassification | None:
        return self.classifier.classify(result)
