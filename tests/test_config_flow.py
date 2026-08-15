"""Tests for GitHub Copilot configuration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.data_entry_flow import FlowResultType

from custom_components.github_copilot.config_flow import GitHubCopilotFlowHandler
from custom_components.github_copilot.const import (
    CONF_API_TOKEN,
    CONF_CLI_URL,
    CONF_CONNECTION_TOKEN,
    CONF_MODEL,
)


def _flow(hass) -> GitHubCopilotFlowHandler:
    flow = GitHubCopilotFlowHandler()
    flow.hass = hass
    flow.context = {"source": "user"}
    return flow


async def test_remote_flow_omits_github_token(hass) -> None:
    """Remote bridge configuration should not retain a GitHub credential."""
    with patch(
        "custom_components.github_copilot.config_flow."
        "GitHubCopilotFlowHandler._test_credentials",
        new=AsyncMock(),
    ):
        result = await _flow(hass).async_step_user(
            {
                CONF_API_TOKEN: "unused-token",
                CONF_MODEL: "auto",
                CONF_CLI_URL: "bridge:8000",
                CONF_CONNECTION_TOKEN: "bridge-secret",
            }
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_API_TOKEN not in result["data"]
    assert result["data"][CONF_CONNECTION_TOKEN] == "bridge-secret"


async def test_remote_flow_requires_connection_token(hass) -> None:
    """Remote bridge configuration should require JSON-RPC authentication."""
    result = await _flow(hass).async_step_user(
        {
            CONF_MODEL: "auto",
            CONF_CLI_URL: "bridge:8000",
            CONF_CONNECTION_TOKEN: "",
        }
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_CONNECTION_TOKEN] == "connection_token_required"


async def test_local_flow_requires_github_token(hass) -> None:
    """Bundled runtime configuration should require explicit authentication."""
    result = await _flow(hass).async_step_user(
        {
            CONF_MODEL: "auto",
            CONF_CLI_URL: "",
            CONF_API_TOKEN: "",
        }
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_API_TOKEN] == "token_required"
