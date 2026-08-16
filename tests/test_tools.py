"""Tests for native Home Assistant tool bridging."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import override
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from copilot import ToolInvocation
from homeassistant.components import conversation
from homeassistant.core import Context, State
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util

from custom_components.github_copilot.tools import (
    GetHistoryTool,
    HomeAssistantToolDispatcher,
    add_history_tool,
    create_copilot_tools,
    prepare_tool_specs,
)


class EchoTool(llm.Tool):
    """Return validated input."""

    name = "Echo"
    description = "Echo a value."
    parameters = vol.Schema({vol.Required("value"): cv.string})

    @override
    async def async_call(
        self,
        _hass,
        tool_input,
        _llm_context,
    ) -> dict[str, object]:
        """Return the tool input."""
        return {"success": True, "value": tool_input.tool_args["value"]}


def _api_instance(hass, tools: list[llm.Tool]) -> llm.APIInstance:
    api = SimpleNamespace(hass=hass, id="assist", name="Assist")
    return llm.APIInstance(
        api=api,
        api_prompt="",
        llm_context=llm.LLMContext(
            platform="conversation",
            context=Context(),
            language="en",
            assistant="github_copilot",
            device_id=None,
        ),
        tools=tools,
        custom_serializer=llm.selector_serializer,
    )


def _chat_log(hass, api_instance: llm.APIInstance) -> conversation.ChatLog:
    return conversation.ChatLog(
        hass=hass,
        conversation_id="conversation-1",
        content=[
            conversation.SystemContent(content="System"),
            conversation.UserContent(content="Run the tool"),
        ],
        llm_api=api_instance,
    )


def test_prepare_tool_specs_converts_schema(hass) -> None:
    """Home Assistant Voluptuous schemas should become SDK JSON schemas."""
    specs, fingerprint = prepare_tool_specs(_api_instance(hass, [EchoTool()]))

    assert len(specs) == 1
    assert specs[0].name == "Echo"
    assert specs[0].parameters["properties"]["value"]["type"] == "string"
    assert specs[0].parameters["required"] == ["value"]
    assert len(fingerprint) == 64


def test_sdk_tools_use_exact_allowlisted_definitions(hass) -> None:
    """Generated SDK tools should bypass only runtime permission prompts."""
    specs, _fingerprint = prepare_tool_specs(_api_instance(hass, [EchoTool()]))
    dispatcher = HomeAssistantToolDispatcher(max_calls=10)

    tools = create_copilot_tools(specs, dispatcher)

    assert [tool.name for tool in tools] == ["Echo"]
    assert tools[0].skip_permission is True
    assert tools[0].defer == "never"


def test_add_history_tool_extends_prepared_chat_log(hass) -> None:
    """History should be added as an explicit HA tool with prompt guidance."""
    chat_log = _chat_log(hass, _api_instance(hass, [EchoTool()]))

    add_history_tool(chat_log)

    assert [tool.name for tool in chat_log.llm_api.tools] == ["Echo", "GetHistory"]
    assert "over the last 24 hours" in chat_log.content[0].content


@pytest.mark.asyncio
async def test_history_tool_summarizes_exposed_numeric_entity(hass) -> None:
    """Recorder samples should become bounded numeric statistics."""
    now = dt_util.utcnow()
    entity_id = "sensor.garage_temperature"
    hass.states.async_set(
        entity_id,
        "80",
        {
            "friendly_name": "Garage Temperature",
            "unit_of_measurement": "°F",
        },
    )
    history = {
        entity_id: [
            State(
                entity_id,
                "70",
                last_updated=now - timedelta(hours=24),
            ),
            State(
                entity_id,
                "80",
                last_updated=now - timedelta(hours=12),
            ),
        ]
    }
    recorder = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=history))
    tool = GetHistoryTool()

    with (
        patch(
            "custom_components.github_copilot.tools.async_should_expose",
            return_value=True,
        ),
        patch(
            "custom_components.github_copilot.tools.get_instance",
            return_value=recorder,
        ),
    ):
        result = await tool.async_call(
            hass,
            llm.ToolInput(
                tool_name="GetHistory",
                tool_args={"entity_id": entity_id, "hours": 24},
            ),
            _api_instance(hass, []).llm_context,
        )

    summary = result["entities"][0]
    assert result["success"] is True
    assert summary["numeric"]["minimum"] == 70
    assert summary["numeric"]["maximum"] == 80
    assert summary["numeric"]["change"] == 10
    assert summary["unit_of_measurement"] == "°F"
    assert "history" not in summary


@pytest.mark.asyncio
async def test_history_tool_rejects_unexposed_entity(hass) -> None:
    """Direct entity IDs must still satisfy Assist exposure."""
    entity_id = "sensor.private_temperature"
    hass.states.async_set(entity_id, "72")
    tool = GetHistoryTool()

    with patch(
        "custom_components.github_copilot.tools.async_should_expose",
        return_value=False,
    ):
        result = await tool.async_call(
            hass,
            llm.ToolInput(
                tool_name="GetHistory",
                tool_args={"entity_id": entity_id},
            ),
            _api_instance(hass, []).llm_context,
        )

    assert result == {
        "success": False,
        "error": "No exposed entities matched the history filters.",
    }


@pytest.mark.asyncio
async def test_dispatcher_executes_through_chat_log(hass) -> None:
    """SDK tool calls should be validated, executed, and recorded by HA."""
    chat_log = _chat_log(hass, _api_instance(hass, [EchoTool()]))
    dispatcher = HomeAssistantToolDispatcher(max_calls=10)
    dispatcher.bind(chat_log, "conversation.github_copilot", max_calls=10)

    result = await dispatcher.async_invoke(
        ToolInvocation(
            tool_name="Echo",
            tool_call_id="call-1",
            arguments={"value": "hello"},
        )
    )

    assert result.result_type == "success"
    assert '"value":"hello"' in result.text_result_for_llm
    assert isinstance(chat_log.content[-2], conversation.AssistantContent)
    assert chat_log.content[-2].tool_calls[0].id == "call-1"
    assert isinstance(chat_log.content[-1], conversation.ToolResultContent)
    assert chat_log.content[-1].tool_result == {
        "success": True,
        "value": "hello",
    }


@pytest.mark.asyncio
async def test_dispatcher_caps_tool_calls(hass) -> None:
    """Attempts beyond the configured turn limit should be rejected."""
    chat_log = _chat_log(hass, _api_instance(hass, [EchoTool()]))
    dispatcher = HomeAssistantToolDispatcher(max_calls=1)
    dispatcher.bind(chat_log, "conversation.github_copilot", max_calls=1)

    first = await dispatcher.async_invoke(
        ToolInvocation(
            tool_name="Echo",
            tool_call_id="call-1",
            arguments={"value": "first"},
        )
    )
    second = await dispatcher.async_invoke(
        ToolInvocation(
            tool_name="Echo",
            tool_call_id="call-2",
            arguments={"value": "second"},
        )
    )

    assert first.result_type == "success"
    assert second.result_type == "rejected"
    assert "tool-call limit" in second.text_result_for_llm


@pytest.mark.asyncio
async def test_dispatcher_blocks_unlocking(hass) -> None:
    """Direct unlock requests should be rejected before HA execution."""
    hass.states.async_set("lock.back_door", "locked")
    chat_log = _chat_log(hass, _api_instance(hass, []))
    dispatcher = HomeAssistantToolDispatcher(max_calls=10)
    dispatcher.bind(chat_log, "conversation.github_copilot", max_calls=10)

    result = await dispatcher.async_invoke(
        ToolInvocation(
            tool_name="HassTurnOff",
            tool_call_id="call-1",
            arguments={"entity_id": "lock.back_door"},
        )
    )

    assert result.result_type == "rejected"
    assert "Unlocking doors is disabled" in result.text_result_for_llm
    assert hass.states.get("lock.back_door").state == "locked"


@pytest.mark.asyncio
async def test_dispatcher_blocks_explicit_unlock_tool(hass) -> None:
    """Provider-specific unlock tools should use the same security policy."""
    hass.states.async_set("lock.back_door", "locked")
    chat_log = _chat_log(hass, _api_instance(hass, []))
    dispatcher = HomeAssistantToolDispatcher(max_calls=10)
    dispatcher.bind(chat_log, "conversation.github_copilot", max_calls=10)

    result = await dispatcher.async_invoke(
        ToolInvocation(
            tool_name="lock__unlock",
            tool_call_id="call-1",
            arguments={"entity_id": "lock.back_door"},
        )
    )

    assert result.result_type == "rejected"
    assert "Unlocking doors is disabled" in result.text_result_for_llm


@pytest.mark.asyncio
async def test_dispatcher_blocks_opening_garage_cover(hass) -> None:
    """Opening garage-class covers should be rejected."""
    hass.states.async_set(
        "cover.garage_door",
        "closed",
        {"device_class": "garage"},
    )
    chat_log = _chat_log(hass, _api_instance(hass, []))
    dispatcher = HomeAssistantToolDispatcher(max_calls=10)
    dispatcher.bind(chat_log, "conversation.github_copilot", max_calls=10)

    result = await dispatcher.async_invoke(
        ToolInvocation(
            tool_name="HassTurnOn",
            tool_call_id="call-1",
            arguments={"entity_id": "cover.garage_door"},
        )
    )

    assert result.result_type == "rejected"
    assert "Opening doors" in result.text_result_for_llm
    assert hass.states.get("cover.garage_door").state == "closed"
