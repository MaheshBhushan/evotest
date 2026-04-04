import os
import shutil
import sys
import tempfile
from uuid import uuid4
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _pick_pytest_tmp_root() -> Path:
    candidates = [
        Path.home() / ".codex" / "memories" / "pytest-runs",
        ROOT / ".tmp" / "pytest-runs",
    ]
    for env_var in ("TMPDIR", "TEMP", "TMP"):
        value = os.environ.get(env_var)
        if value:
            candidates.append(Path(value) / "autotestgen-pytest")
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            continue
    raise RuntimeError("Could not find a writable pytest temp root.")


PYTEST_TMP_ROOT = _pick_pytest_tmp_root()


def pytest_configure(config) -> None:
    os.environ["TMP"] = str(PYTEST_TMP_ROOT)
    os.environ["TEMP"] = str(PYTEST_TMP_ROOT)
    os.environ["TMPDIR"] = str(PYTEST_TMP_ROOT)
    tempfile.tempdir = str(PYTEST_TMP_ROOT)
    _patch_pytest_tmp_cleanup()


@pytest.fixture
def tmp_path():
    path = PYTEST_TMP_ROOT / f"run-{os.getpid()}-{uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _patch_pytest_tmp_cleanup() -> None:
    # In this Windows sandbox, pytest's dead-symlink cleanup can fail with
    # PermissionError on otherwise valid temp roots during session finish.
    from _pytest import pathlib as pytest_pathlib
    from _pytest import tmpdir as pytest_tmpdir

    original = pytest_pathlib.cleanup_dead_symlinks

    def safe_cleanup_dead_symlinks(root) -> None:
        try:
            original(root)
        except PermissionError:
            return

    pytest_pathlib.cleanup_dead_symlinks = safe_cleanup_dead_symlinks
    pytest_tmpdir.cleanup_dead_symlinks = safe_cleanup_dead_symlinks
