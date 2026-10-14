"""Bridge Home Assistant's legacy and typed tool-result APIs."""

from __future__ import annotations

from typing import Any

from homeassistant.components import conversation
from homeassistant.helpers import llm


def create_tool_result(
    data: dict[str, Any], *, error: bool = False
) -> llm.ToolResult | dict[str, Any]:
    """Return the result type supported by the running Home Assistant."""
    if hasattr(llm, "ToolResult"):
        return llm.ToolResult(data=data, error=error)
    return data


def create_tool_result_content(
    *,
    agent_id: str,
    tool_call_id: str,
    tool_name: str,
    data: dict[str, Any],
    error: bool = False,
) -> conversation.ToolResultContent:
    """Record a tool result using the running Home Assistant's API."""
    if hasattr(llm, "ToolResult"):
        return conversation.ToolResultContent(
            agent_id=agent_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            result=llm.ToolResult(data=data, error=error),
        )
    return conversation.ToolResultContent(  # type: ignore[call-arg]
        agent_id=agent_id,
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        tool_result=data,
    )
