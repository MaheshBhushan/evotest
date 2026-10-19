"""Local MCP tools for evaluating trusted projects through evotest."""

from __future__ import annotations

from typing import Any, Literal

from evotest.config import HarnessConfig
from evotest.evaluation import EvaluationRunner


def create_server(config: HarnessConfig):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install MCP support with: python -m pip install -e '.[mcp]'") from exc

    server = FastMCP("evotest-evaluation")
    runner = EvaluationRunner(config)

    @server.tool()
    def evaluate_target(target: str, feedback_format: Literal["compact", "raw"] = "compact") -> dict[str, Any]:
        """Generate, execute and repair tests for a local target; return failures, coverage changes and measured metrics."""
        return runner.evaluate(target, feedback_format)

    @server.tool()
    def compare_feedback(spec: str) -> dict[str, Any]:
        """Compare compact and raw feedback on the same fixed benchmark tasks (1-20) with fresh state per run."""
        return runner.compare(spec)

    return server
