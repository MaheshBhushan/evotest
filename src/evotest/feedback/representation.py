"""Feedback shared by repair prompts and MCP evaluation responses."""

from __future__ import annotations

import json

from evotest.models import AttemptRecord, PromptContext


def attempt_feedback(attempt: AttemptRecord, representation: str) -> dict:
    if representation not in {"compact", "raw"}:
        raise ValueError("Feedback representation must be compact or raw.")
    result = attempt.execution_result
    coverage = result.coverage if result else None
    failure = attempt.failure
    feedback = {
        "attempt": attempt.attempt_number,
        "success": result.success if result else False,
        "exit_code": result.exit_code if result else None,
        "failure": None,
        "coverage_percent": coverage.coverage_percent if coverage else None,
        "missing_lines": coverage.missing_lines if coverage else [],
    }
    if failure:
        feedback["failure"] = {
            "category": failure.category,
            "subtype": failure.subtype,
            "recoverable": failure.recoverable,
            "summary": failure.summary[:240] if representation == "compact" else failure.summary,
        }
    if representation == "raw":
        if failure:
            feedback["failure"]["message"] = failure.message
        feedback.update(stdout=result.stdout if result else "", stderr=result.stderr if result else "")
    else:
        feedback["missing_lines"] = feedback["missing_lines"][:50]
        feedback["missing_lines_truncated"] = bool(coverage and len(coverage.missing_lines) > 50)
    return feedback


def apply_feedback(context: PromptContext, previous: AttemptRecord | None, representation: str) -> None:
    if representation == "default":
        return
    if representation not in {"compact", "raw"}:
        raise ValueError("Feedback representation must be default, compact or raw.")
    if previous is None:
        return
    # Both variants use the same latest attempt, source, strategy and coverage goals.
    failure = previous.failure
    context.previous_failures = []
    if failure:
        detail = failure.summary[:240] if representation == "compact" else failure.message
        context.previous_failures = [f"{failure.subtype}: {detail}"]
    context.execution_evidence = [json.dumps(attempt_feedback(previous, representation), separators=(",", ":"))]
