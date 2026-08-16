"""Tests for the GitHub Copilot AI Task entity."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from copilot import ToolInvocation
from homeassistant.components import ai_task, conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from PIL import Image

from custom_components.github_copilot.ai_task import (
    GitHubCopilotAITaskEntity,
    async_setup_entry,
)
from custom_components.github_copilot.api import (
    CopilotExecutionResult,
    CopilotModelInfo,
    CopilotPromptResult,
    CopilotSessionContext,
    CopilotTurnMetrics,
)
from custom_components.github_copilot.const import (
    CONF_AI_TASK_MODEL,
    CONF_MODEL,
)


def _metrics(content: str = "") -> CopilotTurnMetrics:
    return CopilotTurnMetrics(
        request_duration_ms=500,
        time_to_first_token_ms=200,
        model_duration_ms=450,
        inter_token_latency_ms=15,
        input_tokens=40,
        output_tokens=10,
        input_characters=100,
        response_characters=len(content),
        model="task-model",
    )


def _client() -> MagicMock:
    client = MagicMock()
    client.async_create_session = AsyncMock(
        return_value=CopilotSessionContext(
            session_id="task-session",
            copilot_session=MagicMock(),
            model="task-model",
        )
    )
    client.async_send_prompt = AsyncMock(
        return_value=CopilotPromptResult(
            content="Task response",
            metrics=_metrics("Task response"),
        )
    )
    client.async_execute_prompt = AsyncMock(
        return_value=CopilotExecutionResult(
            content=None,
            metrics=_metrics(),
        )
    )
    client.async_end_session = AsyncMock()
    client.async_get_model_info = AsyncMock(
        return_value=CopilotModelInfo(
            id="task-model",
            name="Task Model",
            supports_vision=True,
            supported_media_types=("image/jpeg",),
            max_prompt_images=1,
            max_prompt_image_size=5 * 1024 * 1024,
        )
    )
    return client


def _entry(client: MagicMock) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "entry-id"
    entry.data = {
        CONF_MODEL: "conversation-model",
        CONF_AI_TASK_MODEL: "task-model",
    }
    entry.runtime_data = SimpleNamespace(client=client)
    return entry


def _entity(hass, client: MagicMock) -> GitHubCopilotAITaskEntity:
    entity = GitHubCopilotAITaskEntity(_entry(client))
    entity.hass = hass
    entity.entity_id = "ai_task.github_copilot"
    return entity


def _chat_log(
    hass,
    instructions: str = "Complete this task",
) -> conversation.ChatLog:
    return conversation.ChatLog(
        hass=hass,
        conversation_id="task-conversation",
        content=[
            conversation.SystemContent(content="System prompt"),
            conversation.UserContent(content=instructions),
        ],
    )


def _structure() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required("should_notify"): cv.boolean,
            vol.Required("category"): vol.In(["normal", "urgent"]),
            vol.Optional("confidence"): vol.All(
                vol.Coerce(float),
                vol.Range(min=0, max=100),
            ),
        },
        extra=vol.PREVENT_EXTRA,
    )


@pytest.mark.asyncio
async def test_platform_registers_generate_data_entity(hass) -> None:
    """The platform should register one data-only AI Task entity."""
    add_entities = MagicMock()

    await async_setup_entry(hass, _entry(_client()), add_entities)

    entity = add_entities.call_args.args[0][0]
    assert isinstance(entity, GitHubCopilotAITaskEntity)
    assert entity.unique_id == "entry-id-ai-task"
    assert entity.supported_features == (
        ai_task.AITaskEntityFeature.GENERATE_DATA
        | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
    )


@pytest.mark.asyncio
async def test_plain_text_task_is_stateless_and_cleaned_up(hass) -> None:
    """Plain text should use one empty-tool session and return ChatLog text."""
    client = _client()
    entity = _entity(hass, client)

    result = await entity._async_generate_data(
        ai_task.GenDataTask(
            name="plain-task",
            instructions="Complete this task",
        ),
        _chat_log(hass),
    )

    assert result.data == "Task response"
    create_kwargs = client.async_create_session.await_args.kwargs
    assert create_kwargs["model"] == "task-model"
    assert create_kwargs["tools"] == []
    client.async_end_session.assert_awaited_once_with("task-session")


@pytest.mark.asyncio
async def test_structured_task_accepts_valid_first_submission(hass) -> None:
    """A valid terminal submission should return native structured data."""
    client = _client()

    async def execute(*_args, **_kwargs):
        tool = client.async_create_session.await_args.kwargs["tools"][0]
        tool_result = await tool.handler(
            ToolInvocation(
                tool_name="submit_result",
                tool_call_id="call-1",
                arguments={
                    "should_notify": True,
                    "category": "urgent",
                    "confidence": 92,
                },
            )
        )
        assert tool_result.result_type == "success"
        return CopilotExecutionResult(content=None, metrics=_metrics())

    client.async_execute_prompt.side_effect = execute
    chat_log = _chat_log(hass)
    result = await _entity(hass, client)._async_generate_data(
        ai_task.GenDataTask(
            name="structured-task",
            instructions="Classify this",
            structure=_structure(),
        ),
        chat_log,
    )

    assert result.data == {
        "should_notify": True,
        "category": "urgent",
        "confidence": 92.0,
    }
    tool = client.async_create_session.await_args.kwargs["tools"][0]
    assert tool.name == "submit_result"
    assert tool.is_terminal is True
    assert tool.skip_permission is True
    assert len(client.async_create_session.await_args.kwargs["tools"]) == 1
    assert isinstance(chat_log.content[-1], conversation.ToolResultContent)


@pytest.mark.asyncio
async def test_structured_task_repairs_one_invalid_submission(hass) -> None:
    """One invalid result should receive feedback before a valid repair."""
    client = _client()

    async def execute(*_args, **_kwargs):
        tool = client.async_create_session.await_args.kwargs["tools"][0]
        first = await tool.handler(
            ToolInvocation(
                tool_name="submit_result",
                tool_call_id="call-1",
                arguments={"category": "invalid"},
            )
        )
        second = await tool.handler(
            ToolInvocation(
                tool_name="submit_result",
                tool_call_id="call-2",
                arguments={
                    "should_notify": False,
                    "category": "normal",
                },
            )
        )
        assert first.result_type == "failure"
        assert second.result_type == "success"
        return CopilotExecutionResult(content=None, metrics=_metrics())

    client.async_execute_prompt.side_effect = execute
    result = await _entity(hass, client)._async_generate_data(
        ai_task.GenDataTask(
            name="repair-task",
            instructions="Classify this",
            structure=_structure(),
        ),
        _chat_log(hass),
    )

    assert result.data == {
        "should_notify": False,
        "category": "normal",
    }


@pytest.mark.asyncio
async def test_structured_task_fails_after_second_invalid_submission(hass) -> None:
    """A second invalid result should terminate and surface an error."""
    client = _client()

    async def execute(*_args, **_kwargs):
        tool = client.async_create_session.await_args.kwargs["tools"][0]
        await tool.handler(
            ToolInvocation(
                tool_name="submit_result",
                tool_call_id="call-1",
                arguments={},
            )
        )
        second = await tool.handler(
            ToolInvocation(
                tool_name="submit_result",
                tool_call_id="call-2",
                arguments={"category": "invalid"},
            )
        )
        assert second.result_type == "success"
        return CopilotExecutionResult(content=None, metrics=_metrics())

    client.async_execute_prompt.side_effect = execute
    with pytest.raises(HomeAssistantError, match="after one repair"):
        await _entity(hass, client)._async_generate_data(
            ai_task.GenDataTask(
                name="invalid-task",
                instructions="Classify this",
                structure=_structure(),
            ),
            _chat_log(hass),
        )

    client.async_end_session.assert_awaited_once_with("task-session")


@pytest.mark.asyncio
async def test_structured_task_requires_submit_result(hass) -> None:
    """Structured prose without a tool submission should fail."""
    client = _client()

    with pytest.raises(HomeAssistantError, match="did not submit"):
        await _entity(hass, client)._async_generate_data(
            ai_task.GenDataTask(
                name="missing-tool",
                instructions="Classify this",
                structure=_structure(),
            ),
            _chat_log(hass),
        )


@pytest.mark.asyncio
async def test_plain_text_vision_task_passes_normalized_blob(
    hass,
    tmp_path,
) -> None:
    """AI Tasks should validate images and pass SDK blobs without paths."""
    client = _client()
    image_path = tmp_path / "image.jpg"

    Image.new("RGB", (32, 32), "blue").save(image_path, format="JPEG")
    task = ai_task.GenDataTask(
        name="attachment-task",
        instructions="Describe this image",
        attachments=[
            conversation.Attachment(
                media_content_id="media-source://camera/camera.test",
                mime_type="image/jpeg",
                path=image_path,
            )
        ],
    )

    result = await _entity(hass, client)._async_generate_data(task, _chat_log(hass))

    assert result.data == "Task response"
    attachments = client.async_send_prompt.await_args.kwargs["attachments"]
    assert len(attachments) == 1
    assert attachments[0]["type"] == "blob"
    assert attachments[0]["mimeType"] == "image/jpeg"
    assert "path" not in attachments[0]


@pytest.mark.asyncio
async def test_structured_vision_task_preserves_submit_only_tools(
    hass,
    tmp_path,
) -> None:
    """Vision should not add tools beyond the structured result tool."""
    client = _client()
    image_path = tmp_path / "image.jpg"

    Image.new("RGB", (32, 32), "green").save(image_path, format="JPEG")

    async def execute(*_args, **_kwargs):
        tools = client.async_create_session.await_args.kwargs["tools"]
        assert [tool.name for tool in tools] == ["submit_result"]
        assert len(_kwargs["attachments"]) == 1
        await tools[0].handler(
            ToolInvocation(
                tool_name="submit_result",
                tool_call_id="call-1",
                arguments={
                    "should_notify": False,
                    "category": "normal",
                },
            )
        )
        return CopilotExecutionResult(content=None, metrics=_metrics())

    client.async_execute_prompt.side_effect = execute
    result = await _entity(hass, client)._async_generate_data(
        ai_task.GenDataTask(
            name="vision-structured",
            instructions="Classify this image",
            structure=_structure(),
            attachments=[
                conversation.Attachment(
                    media_content_id="media-source://camera/camera.test",
                    mime_type="image/jpeg",
                    path=image_path,
                )
            ],
        ),
        _chat_log(hass),
    )

    assert result.data == {
        "should_notify": False,
        "category": "normal",
    }
