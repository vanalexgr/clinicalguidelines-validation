"""Optional MCP relay client placeholder."""

from __future__ import annotations

from src.agent_client.base import AgentClient, DryRunResult
from src.common.schemas import AgentAnswer, BenchmarkItem


class MCPAgentClient(AgentClient):
    """Stub for a future MCP relay integration."""

    @property
    def cache_model_id(self) -> str:
        return "mcp-agent"

    def generate_answer(self, item: BenchmarkItem, *, run_index: int = 0) -> AgentAnswer:
        raise NotImplementedError(
            "[AUTHOR ACTION] MCP relay client is not configured for this repository."
        )

    def dry_run(self, item: BenchmarkItem) -> DryRunResult:
        raise NotImplementedError(
            "[AUTHOR ACTION] MCP relay client is not configured for this repository."
        )
