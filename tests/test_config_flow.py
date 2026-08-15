"""Tests for GitHub Copilot configuration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.github_copilot.config_flow import (
    GitHubCopilotFlowHandler,
    GitHubCopilotOptionsFlow,
)
from custom_components.github_copilot.const import (
    CONF_API_TOKEN,
    CONF_CLI_URL,
    CONF_CONNECTION_TOKEN,
    CONF_MODEL,
    CONF_SESSION_TIMEOUT,
    DEFAULT_PROMPT,
    DEFAULT_SESSION_TIMEOUT,
    DOMAIN,
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
    assert result["data"]["prompt"] == DEFAULT_PROMPT
    assert result["data"][CONF_SESSION_TIMEOUT] == DEFAULT_SESSION_TIMEOUT


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


async def test_options_form_includes_conversation_settings(hass) -> None:
    """Options should expose prompt and inactivity timeout controls."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_MODEL: "auto",
            CONF_CLI_URL: "bridge:8000",
            CONF_CONNECTION_TOKEN: "bridge-secret",
        },
    )
    entry.add_to_hass(hass)

    flow = GitHubCopilotOptionsFlow()
    flow.hass = hass
    flow.handler = entry.entry_id
    result = await flow.async_step_init()

    assert result["type"] is FlowResultType.FORM
    schema_keys = {str(key) for key in result["data_schema"].schema}
    assert "prompt" in schema_keys
    assert "session_timeout" in schema_keys
