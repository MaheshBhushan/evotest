from pathlib import Path

from evotest.feedback import FailureClassifier
from evotest.models import ExecutionResult


def test_classifier_detects_import_error() -> None:
    result = ExecutionResult(
        target_id="pkg/maths.py::add",
        attempt_number=1,
        success=False,
        exit_code=2,
        stdout="",
        stderr="ImportError: cannot import name 'add' from 'pkg.maths'",
        duration_seconds=0.1,
        coverage=None,
        test_file_path=Path("test_generated.py"),
    )

    failure = FailureClassifier().classify(result)

    assert failure is not None
    assert failure.category == "import_dependency"
    assert failure.subtype in {"import_error", "missing_dependency"}
    assert failure.recoverable is True


def test_classifier_detects_permission_error() -> None:
    result = ExecutionResult(
        target_id="pkg/io.py::load",
        attempt_number=2,
        success=False,
        exit_code=1,
        stdout="",
        stderr="PermissionError: [Errno 13] permission denied",
        duration_seconds=0.1,
        coverage=None,
        test_file_path=Path("test_generated.py"),
    )

    failure = FailureClassifier().classify(result)

    assert failure is not None
    assert failure.category == "environment_integration"
    assert failure.subtype == "permission_error"
    assert failure.recoverable is False
