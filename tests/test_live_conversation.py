"""Opt-in live Home Assistant conversation smoke test."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.components import conversation
from homeassistant.const import CONF_PROMPT
from homeassistant.core import Context

from custom_components.github_copilot.api import GitHubCopilotApiClient
from custom_components.github_copilot.conversation import (
    GitHubCopilotConversationEntity,
)

RUNTIME_URL = os.environ.get("COPILOT_RUNTIME_URL", "")
CONNECTION_TOKEN = os.environ.get("COPILOT_CONNECTION_TOKEN", "")

pytestmark = pytest.mark.skipif(
    not RUNTIME_URL or not CONNECTION_TOKEN,
    reason="Live Copilot bridge credentials are not configured",
)


@pytest.mark.asyncio
async def test_live_home_assistant_conversation(hass, socket_enabled) -> None:
    """Exercise the real SDK through Home Assistant's ChatLog path."""
    client = GitHubCopilotApiClient(
        model=os.environ.get("COPILOT_SMOKE_MODEL", "gpt-5-mini"),
        cli_url=RUNTIME_URL,
        connection_token=CONNECTION_TOKEN,
    )
    entry = MagicMock()
    entry.entry_id = "live-entry"
    entry.data = {
        CONF_PROMPT: (
            "You are a Home Assistant voice agent. Follow exact response-format "
            "requests. Use plain text."
        )
    }
    entry.runtime_data = SimpleNamespace(client=client)
    entity = GitHubCopilotConversationEntity(entry)
    entity.hass = hass

    try:
        first = await entity.async_process(
            conversation.ConversationInput(
                text="Reply with exactly LIVE_ONE",
                context=Context(),
                conversation_id="phase2-live",
                device_id=None,
                satellite_id=None,
                language="en",
                agent_id="conversation.github_copilot",
            )
        )
        second = await entity.async_process(
            conversation.ConversationInput(
                text=(
                    "What exact token did you reply with previously? "
                    "Reply with that token only."
                ),
                context=Context(),
                conversation_id=first.conversation_id,
                device_id=None,
                satellite_id=None,
                language="en",
                agent_id="conversation.github_copilot",
            )
        )
    finally:
        await entity._async_discard_session("phase2-live")
        await client.async_close()

    assert first.response.speech["plain"]["speech"].strip() == "LIVE_ONE"
    assert second.response.speech["plain"]["speech"].strip() == "LIVE_ONE"
