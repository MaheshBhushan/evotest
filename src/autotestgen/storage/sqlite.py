from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from autotestgen.models import (
    AttemptRecord,
    ExecutionResult,
    FailureClassification,
    FunctionTarget,
    ModuleTarget,
)


class SQLiteStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS targets (
                    target_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    language TEXT,
                    adapter_id TEXT,
                    module_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    symbol_name TEXT,
                    qualname TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS prompts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    prompt_text TEXT NOT NULL,
                    prompt_version TEXT,
                    strategy TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(target_id, attempt_number)
                );

                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    language TEXT,
                    adapter_id TEXT,
                    generated_test_path TEXT NOT NULL,
                    generated_test_source TEXT NOT NULL,
                    generation_mode TEXT,
                    parent_attempt_number INTEGER,
                    targeted_uncovered_lines_json TEXT,
                    provider_name TEXT,
                    model_name TEXT,
                    fallback_reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(target_id, attempt_number)
                );

                CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    language TEXT,
                    adapter_id TEXT,
                    success INTEGER NOT NULL,
                    exit_code INTEGER NOT NULL,
                    stdout TEXT NOT NULL,
                    stderr TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    coverage_percent REAL,
                    coverage_payload_json TEXT,
                    test_file_path TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(target_id, attempt_number)
                );

                CREATE TABLE IF NOT EXISTS failure_classifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    subtype TEXT NOT NULL,
                    recoverable INTEGER NOT NULL,
                    message TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(target_id, attempt_number)
                );
                """
            )
            self._ensure_column(connection, "prompts", "prompt_version", "TEXT")
            self._ensure_column(connection, "prompts", "strategy", "TEXT")
            self._ensure_column(connection, "targets", "language", "TEXT")
            self._ensure_column(connection, "targets", "adapter_id", "TEXT")
            self._ensure_column(connection, "attempts", "language", "TEXT")
            self._ensure_column(connection, "attempts", "adapter_id", "TEXT")
            self._ensure_column(connection, "results", "language", "TEXT")
            self._ensure_column(connection, "results", "adapter_id", "TEXT")
            self._ensure_column(connection, "attempts", "generation_mode", "TEXT")
            self._ensure_column(connection, "attempts", "parent_attempt_number", "INTEGER")
            self._ensure_column(connection, "attempts", "targeted_uncovered_lines_json", "TEXT")
            self._ensure_column(connection, "attempts", "provider_name", "TEXT")
            self._ensure_column(connection, "attempts", "model_name", "TEXT")
            self._ensure_column(connection, "attempts", "fallback_reason", "TEXT")

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {row["name"] for row in rows}
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def upsert_function_target(self, target: FunctionTarget) -> None:
        payload = {
            "line_start": target.line_start,
            "line_end": target.line_end,
            "source": target.source,
            "imports": [asdict(import_ref) for import_ref in target.imports],
            "decorators": target.decorators,
            "is_public": target.is_public,
            "dependency_hints": target.dependency_hints,
            "complexity_score": target.complexity_score,
            "io_hint": target.io_hint,
            "target_score": target.target_score,
            "owner_qualname": target.owner_qualname,
            "method_kind": target.method_kind,
            "constructor_parameters": target.constructor_parameters,
        }
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO targets (
                    target_id, kind, language, adapter_id, module_name, file_path, symbol_name, qualname, payload_json
                ) VALUES (?, 'function', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_id) DO UPDATE SET
                    language = excluded.language,
                    adapter_id = excluded.adapter_id,
                    module_name = excluded.module_name,
                    file_path = excluded.file_path,
                    symbol_name = excluded.symbol_name,
                    qualname = excluded.qualname,
                    payload_json = excluded.payload_json
                """,
                (
                    target.target_id,
                    target.language,
                    target.adapter_id,
                    target.module_name,
                    str(target.file_path),
                    target.symbol_name,
                    target.qualname,
                    json.dumps(payload),
                ),
            )

    def upsert_module_target(self, target: ModuleTarget) -> None:
        payload = {
            "source": target.source,
            "imports": [asdict(import_ref) for import_ref in target.imports],
            "public_symbols": target.public_symbols,
            "complexity_score": target.complexity_score,
            "io_hint": target.io_hint,
            "target_score": target.target_score,
        }
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO targets (
                    target_id, kind, language, adapter_id, module_name, file_path, payload_json
                ) VALUES (?, 'module', ?, ?, ?, ?, ?)
                ON CONFLICT(target_id) DO UPDATE SET
                    language = excluded.language,
                    adapter_id = excluded.adapter_id,
                    module_name = excluded.module_name,
                    file_path = excluded.file_path,
                    payload_json = excluded.payload_json
                """,
                (
                    target.target_id,
                    target.language,
                    target.adapter_id,
                    target.module_name,
                    str(target.file_path),
                    json.dumps(payload),
                ),
            )

    def save_attempt(self, attempt: AttemptRecord) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO prompts (target_id, attempt_number, prompt_text, prompt_version, strategy)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(target_id, attempt_number) DO UPDATE SET
                    prompt_text = excluded.prompt_text,
                    prompt_version = excluded.prompt_version,
                    strategy = excluded.strategy
                """,
                (
                    attempt.target_id,
                    attempt.attempt_number,
                    attempt.prompt_text,
                    attempt.prompt_version,
                    attempt.strategy,
                ),
            )
            connection.execute(
                """
                INSERT INTO attempts (
                    target_id, attempt_number, language, adapter_id, generated_test_path, generated_test_source,
                    generation_mode, parent_attempt_number, targeted_uncovered_lines_json,
                    provider_name, model_name, fallback_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_id, attempt_number) DO UPDATE SET
                    language = excluded.language,
                    adapter_id = excluded.adapter_id,
                    generated_test_path = excluded.generated_test_path,
                    generated_test_source = excluded.generated_test_source,
                    generation_mode = excluded.generation_mode,
                    parent_attempt_number = excluded.parent_attempt_number,
                    targeted_uncovered_lines_json = excluded.targeted_uncovered_lines_json,
                    provider_name = excluded.provider_name,
                    model_name = excluded.model_name,
                    fallback_reason = excluded.fallback_reason
                """,
                (
                    attempt.target_id,
                    attempt.attempt_number,
                    attempt.language,
                    attempt.adapter_id,
                    str(attempt.generated_test_path),
                    attempt.generated_test_source,
                    attempt.generation_mode,
                    attempt.parent_attempt_number,
                    json.dumps(attempt.targeted_uncovered_lines),
                    attempt.provider_name,
                    attempt.model_name,
                    attempt.fallback_reason,
                ),
            )

    def save_execution_result(self, result: ExecutionResult) -> None:
        coverage_payload = None
        coverage_percent = None
        if result.coverage:
            coverage_payload = json.dumps(
                {
                    "statements": result.coverage.statements,
                    "missing": result.coverage.missing,
                    "coverage_percent": result.coverage.coverage_percent,
                    "missing_lines": result.coverage.missing_lines,
                }
            )
            coverage_percent = result.coverage.coverage_percent
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO results (
                    target_id, attempt_number, language, adapter_id, success, exit_code, stdout, stderr,
                    duration_seconds, coverage_percent, coverage_payload_json, test_file_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_id, attempt_number) DO UPDATE SET
                    language = excluded.language,
                    adapter_id = excluded.adapter_id,
                    success = excluded.success,
                    exit_code = excluded.exit_code,
                    stdout = excluded.stdout,
                    stderr = excluded.stderr,
                    duration_seconds = excluded.duration_seconds,
                    coverage_percent = excluded.coverage_percent,
                    coverage_payload_json = excluded.coverage_payload_json,
                    test_file_path = excluded.test_file_path
                """,
                (
                    result.target_id,
                    result.attempt_number,
                    result.language,
                    result.adapter_id,
                    int(result.success),
                    result.exit_code,
                    result.stdout,
                    result.stderr,
                    result.duration_seconds,
                    coverage_percent,
                    coverage_payload,
                    str(result.test_file_path),
                ),
            )

    def save_failure(self, target_id: str, attempt_number: int, failure: FailureClassification) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO failure_classifications (
                    target_id, attempt_number, category, subtype, recoverable, message, summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_id, attempt_number) DO UPDATE SET
                    category = excluded.category,
                    subtype = excluded.subtype,
                    recoverable = excluded.recoverable,
                    message = excluded.message,
                    summary = excluded.summary
                """,
                (
                    target_id,
                    attempt_number,
                    failure.category,
                    failure.subtype,
                    int(failure.recoverable),
                    failure.message,
                    failure.summary,
                ),
            )

    def load_attempt_failures(self, target_id: str) -> list[FailureClassification]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT category, subtype, recoverable, message, summary
                FROM failure_classifications
                WHERE target_id = ?
                ORDER BY attempt_number ASC
                """,
                (target_id,),
            ).fetchall()
        return [
            FailureClassification(
                category=row["category"],
                subtype=row["subtype"],
                recoverable=bool(row["recoverable"]),
                message=row["message"],
                summary=row["summary"],
            )
            for row in rows
        ]

    def load_successful_attempt_examples(
        self,
        *,
        language: str,
        adapter_id: str | None = None,
        exclude_target_id: str | None = None,
        limit: int = 2,
    ) -> list[tuple[str, str]]:
        query = """
            SELECT attempts.target_id, attempts.generated_test_source
            FROM attempts
            INNER JOIN results
                ON results.target_id = attempts.target_id
               AND results.attempt_number = attempts.attempt_number
            WHERE results.success = 1
              AND attempts.language = ?
        """
        params: list[object] = [language]
        if adapter_id is not None:
            query += " AND attempts.adapter_id = ?"
            params.append(adapter_id)
        if exclude_target_id is not None:
            query += " AND attempts.target_id != ?"
            params.append(exclude_target_id)
        query += " ORDER BY results.id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [
            (str(row["target_id"]), str(row["generated_test_source"]))
            for row in rows
            if row["generated_test_source"]
        ]
