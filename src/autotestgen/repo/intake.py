from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .models import RepositorySource


class RepositoryIntake:
    def prepare_workspace(self, source: RepositorySource, workspace_root: Path) -> Path:
        workspace_root.mkdir(parents=True, exist_ok=True)
        clone_root = workspace_root / "repo"
        if clone_root.exists():
            shutil.rmtree(clone_root)
        local_source = Path(source.raw)
        if local_source.exists():
            return self._prepare_local(local_source.resolve(), clone_root, source)
        return self._prepare_remote(source, clone_root)

    @staticmethod
    def workspace_name(source: str) -> str:
        slug_base = Path(source.rstrip("/")).stem or "repo"
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", slug_base).strip("._") or "repo"
        return f"{slug}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"

    def _prepare_local(self, source_path: Path, clone_root: Path, source: RepositorySource) -> Path:
        if (source_path / ".git").exists() and (source.branch or source.commit or source.ref):
            self._git_clone(str(source_path), clone_root, branch=source.branch)
            self._checkout_requested_ref(clone_root, source)
            return clone_root
        shutil.copytree(
            source_path,
            clone_root,
            dirs_exist_ok=False,
            ignore=shutil.ignore_patterns(".autotestgen", "__pycache__", ".pytest_cache", ".tmp"),
        )
        return clone_root

    def _prepare_remote(self, source: RepositorySource, clone_root: Path) -> Path:
        self._git_clone(source.raw, clone_root, branch=source.branch)
        self._checkout_requested_ref(clone_root, source)
        return clone_root

    @staticmethod
    def _git_clone(source: str, destination: Path, *, branch: str | None = None) -> None:
        command = ["git", "clone", source, str(destination)]
        if branch:
            command = ["git", "clone", "--branch", branch, source, str(destination)]
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError((completed.stdout or "").strip() or f"git clone failed for {source}")

    @staticmethod
    def _checkout_requested_ref(clone_root: Path, source: RepositorySource) -> None:
        if source.ref:
            fetch = subprocess.run(
                ["git", "fetch", "origin", source.ref],
                cwd=clone_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if fetch.returncode != 0:
                raise RuntimeError((fetch.stdout or "").strip() or f"git fetch failed for {source.ref}")
            checkout_target = "FETCH_HEAD"
        elif source.commit:
            checkout_target = source.commit
        else:
            return
        checkout = subprocess.run(
            ["git", "checkout", checkout_target],
            cwd=clone_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if checkout.returncode != 0:
            raise RuntimeError((checkout.stdout or "").strip() or f"git checkout failed for {checkout_target}")
