"""Tests for the Home Assistant conversation entity."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import conversation
from homeassistant.const import CONF_LLM_HASS_API
from homeassistant.core import Context
from homeassistant.helpers import intent

from custom_components.github_copilot.api import (
    CopilotPromptResult,
    CopilotSessionContext,
    CopilotTurnMetrics,
    GitHubCopilotApiClientCommunicationError,
)
from custom_components.github_copilot.const import (
    CONF_SESSION_TIMEOUT,
    DEFAULT_PROMPT,
)
from custom_components.github_copilot.conversation import (
    GitHubCopilotConversationEntity,
    _bounded_transcript,
)


def _entry(client: MagicMock, data: dict | None = None) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "entry-id"
    entry.data = data or {}
    entry.runtime_data = SimpleNamespace(client=client)
    return entry


def _prompt_result(content: str = "Copilot response") -> CopilotPromptResult:
    return CopilotPromptResult(
        content=content,
        metrics=CopilotTurnMetrics(
            request_duration_ms=750,
            time_to_first_token_ms=250,
            model_duration_ms=700,
            inter_token_latency_ms=20,
            input_tokens=100,
            output_tokens=12,
            input_characters=50,
            response_characters=len(content),
            model="gpt-5-mini",
        ),
    )


def _client() -> MagicMock:
    client = MagicMock()
    client.async_create_session = AsyncMock(
        side_effect=lambda _prompt, **_kwargs: CopilotSessionContext(
            session_id=f"sdk-{client.async_create_session.await_count}",
            copilot_session=MagicMock(),
        )
    )

    async def send_prompt(*_args, on_delta=None, **_kwargs):
        if on_delta is not None:
            on_delta("Copilot ")
            on_delta("response")
        return _prompt_result()

    client.async_send_prompt = AsyncMock(side_effect=send_prompt)
    client.async_end_session = AsyncMock()
    return client


def _input(text: str, conversation_id: str = "conversation-1"):
    return conversation.ConversationInput(
        text=text,
        context=Context(),
        conversation_id=conversation_id,
        device_id="voice-device",
        satellite_id=None,
        language="en",
        agent_id="conversation.github_copilot",
    )


def _chat_log(
    hass,
    conversation_id: str,
    *,
    current_user: str,
    prior: list[conversation.Content] | None = None,
) -> conversation.ChatLog:
    return conversation.ChatLog(
        hass=hass,
        conversation_id=conversation_id,
        content=[
            conversation.SystemContent(content=""),
            *(prior or []),
            conversation.UserContent(content=current_user),
        ],
    )


def _provide_prompt(rendered_prompt: str):
    async def provide(chat_log, *_args, **_kwargs) -> None:
        chat_log.content[0] = conversation.SystemContent(content=rendered_prompt)

    return provide


@pytest.mark.asyncio
async def test_success_uses_chat_log_result(hass) -> None:
    """Successful output should be added to ChatLog and returned by HA."""
    client = _client()
    entity = GitHubCopilotConversationEntity(_entry(client))
    chat_log = _chat_log(hass, "conversation-1", current_user="Hello")
    rendered = (
        f"{DEFAULT_PROMPT}\nCurrent time is 10:00:00. Today's date is 2026-08-15.\n"
    )

    with patch.object(
        conversation.ChatLog,
        "async_provide_llm_data",
        new=_provide_prompt(rendered),
    ):
        result = await entity._async_handle_message(_input("Hello"), chat_log)

    assert isinstance(chat_log.content[-1], conversation.AssistantContent)
    assert chat_log.content[-1].content == "Copilot response"
    assert result.conversation_id == "conversation-1"
    assert result.response.speech["plain"]["speech"] == "Copilot response"
    client.async_create_session.assert_awaited_once_with(
        DEFAULT_PROMPT.strip(),
        tools=[],
    )
    assert entity.supported_features == 0
    assert entity.supports_streaming is True
    assert entity.extra_state_attributes == {
        "model": "gpt-5-mini",
        "request_duration_ms": 750,
        "time_to_first_token_ms": 250,
        "model_duration_ms": 700,
        "inter_token_latency_ms": 20,
        "input_tokens": 100,
        "output_tokens": 12,
        "input_characters": 50,
        "response_characters": 16,
        "session_reused": False,
    }
    turn_context = client.async_send_prompt.await_args.kwargs["turn_context"]
    assert "Current time is " in turn_context
    assert "Prior conversation transcript" not in turn_context


@pytest.mark.asyncio
async def test_control_mode_uses_nonstreaming_final_response(hass) -> None:
    """Configured HA tools should enable control and preserve ChatLog ordering."""
    client = _client()
    entity = GitHubCopilotConversationEntity(
        _entry(client, {CONF_LLM_HASS_API: ["assist"]})
    )
    chat_log = _chat_log(hass, "conversation-1", current_user="Turn it on")

    async def provide(chat_log, *_args, **_kwargs) -> None:
        chat_log.content[0] = conversation.SystemContent(content=DEFAULT_PROMPT)
        chat_log.llm_api = SimpleNamespace(tools=[], custom_serializer=None)

    with patch.object(
        conversation.ChatLog,
        "async_provide_llm_data",
        new=provide,
    ):
        result = await entity._async_handle_message(_input("Turn it on"), chat_log)

    assert entity.supported_features == conversation.ConversationEntityFeature.CONTROL
    assert result.response.speech["plain"]["speech"] == "Copilot response"
    assert client.async_send_prompt.await_args.kwargs.get("on_delta") is None
    assert chat_log.content[-1].content == "Copilot response"


@pytest.mark.asyncio
async def test_time_changes_do_not_replace_session(hass) -> None:
    """Per-turn time changes must not invalidate a stable SDK session."""
    client = _client()
    entity = GitHubCopilotConversationEntity(_entry(client))
    first_log = _chat_log(hass, "conversation-1", current_user="First")
    second_log = _chat_log(
        hass,
        "conversation-1",
        current_user="Second",
        prior=[
            conversation.UserContent(content="First"),
            conversation.AssistantContent(
                agent_id="conversation.github_copilot",
                content="First response",
            ),
        ],
    )

    async def provide_dynamic(chat_log, *_args, **_kwargs) -> None:
        second = len(client.async_send_prompt.await_args_list) > 0
        time_value = "10:00:01" if second else "10:00:00"
        chat_log.content[0] = conversation.SystemContent(
            content=(
                f"{DEFAULT_PROMPT}\nCurrent time is {time_value}. "
                "Today's date is 2026-08-15.\n"
            )
        )

    with patch.object(
        conversation.ChatLog,
        "async_provide_llm_data",
        new=provide_dynamic,
    ):
        await entity._async_handle_message(_input("First"), first_log)
        await entity._async_handle_message(_input("Second"), second_log)

    assert client.async_create_session.await_count == 1
    assert client.async_send_prompt.await_count == 2


@pytest.mark.asyncio
async def test_prompt_change_recreates_with_bounded_transcript(hass) -> None:
    """Stable prompt changes should recreate and restore recent ChatLog context."""
    client = _client()
    entity = GitHubCopilotConversationEntity(_entry(client))
    first_log = _chat_log(hass, "conversation-1", current_user="First")
    second_log = _chat_log(
        hass,
        "conversation-1",
        current_user="Follow up",
        prior=[
            conversation.UserContent(content="First"),
            conversation.AssistantContent(
                agent_id="conversation.github_copilot",
                content="First response",
            ),
        ],
    )
    prompts = iter(
        [
            (
                f"{DEFAULT_PROMPT}\nCurrent time is 10:00:00. "
                "Today's date is 2026-08-15.\n"
            ),
            (
                "Different instructions\nCurrent time is 10:01:00. "
                "Today's date is 2026-08-15.\n"
            ),
        ]
    )

    async def provide(chat_log, *_args, **_kwargs) -> None:
        chat_log.content[0] = conversation.SystemContent(content=next(prompts))

    with patch.object(
        conversation.ChatLog,
        "async_provide_llm_data",
        new=provide,
    ):
        await entity._async_handle_message(_input("First"), first_log)
        await entity._async_handle_message(_input("Follow up"), second_log)

    assert client.async_create_session.await_count == 2
    client.async_end_session.assert_awaited_once_with("sdk-1")
    turn_context = client.async_send_prompt.await_args.kwargs["turn_context"]
    assert "Prior conversation transcript" in turn_context
    assert "User: First" in turn_context
    assert "Assistant: First response" in turn_context


@pytest.mark.asyncio
async def test_tool_definition_change_recreates_session(hass) -> None:
    """Changed Home Assistant tool catalogs should replace the SDK session."""
    client = _client()
    entity = GitHubCopilotConversationEntity(
        _entry(client, {CONF_LLM_HASS_API: ["assist"]})
    )

    async def provide(chat_log, *_args, **_kwargs) -> None:
        chat_log.content[0] = conversation.SystemContent(content=DEFAULT_PROMPT)
        chat_log.llm_api = SimpleNamespace(tools=[], custom_serializer=None)

    with (
        patch.object(
            conversation.ChatLog,
            "async_provide_llm_data",
            new=provide,
        ),
        patch(
            "custom_components.github_copilot.conversation.prepare_tool_specs",
            side_effect=[([], "tools-one"), ([], "tools-two")],
        ),
    ):
        await entity._async_handle_message(
            _input("First"),
            _chat_log(hass, "conversation-1", current_user="First"),
        )
        await entity._async_handle_message(
            _input("Second"),
            _chat_log(hass, "conversation-1", current_user="Second"),
        )

    assert client.async_create_session.await_count == 2
    client.async_end_session.assert_awaited_once_with("sdk-1")


@pytest.mark.asyncio
async def test_same_conversation_turns_are_serialized(hass) -> None:
    """Overlapping turns for one conversation should use one request lock."""
    client = _client()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_started = asyncio.Event()

    async def send_prompt(*_args, **_kwargs) -> CopilotPromptResult:
        on_delta = _kwargs["on_delta"]
        if not first_started.is_set():
            first_started.set()
            await release_first.wait()
            on_delta("First response")
            return _prompt_result("First response")
        second_started.set()
        on_delta("Second response")
        return _prompt_result("Second response")

    client.async_send_prompt.side_effect = send_prompt
    entity = GitHubCopilotConversationEntity(_entry(client))

    with patch.object(
        conversation.ChatLog,
        "async_provide_llm_data",
        new=_provide_prompt(
            f"{DEFAULT_PROMPT}\nCurrent time is 10:00:00. Today's date is 2026-08-15.\n"
        ),
    ):
        first_task = asyncio.create_task(
            entity._async_handle_message(
                _input("First"),
                _chat_log(hass, "conversation-1", current_user="First"),
            )
        )
        await first_started.wait()
        second_task = asyncio.create_task(
            entity._async_handle_message(
                _input("Second"),
                _chat_log(hass, "conversation-1", current_user="Second"),
            )
        )
        await asyncio.sleep(0)
        assert not second_started.is_set()
        release_first.set()
        await asyncio.gather(first_task, second_task)

    assert second_started.is_set()
    assert client.async_create_session.await_count == 1


@pytest.mark.asyncio
async def test_final_response_suffix_is_reconciled_into_stream(hass) -> None:
    """A final SDK suffix should be added once when the last delta is incomplete."""
    client = _client()

    async def send_prompt(*_args, on_delta=None, **_kwargs):
        assert on_delta is not None
        on_delta("Partial")
        return _prompt_result("Partial response")

    client.async_send_prompt.side_effect = send_prompt
    entity = GitHubCopilotConversationEntity(_entry(client))
    chat_log = _chat_log(hass, "conversation-1", current_user="Hello")

    with patch.object(
        conversation.ChatLog,
        "async_provide_llm_data",
        new=_provide_prompt(DEFAULT_PROMPT),
    ):
        result = await entity._async_handle_message(_input("Hello"), chat_log)

    assert chat_log.content[-1].content == "Partial response"
    assert result.response.speech["plain"]["speech"] == "Partial response"


@pytest.mark.asyncio
async def test_mismatched_final_response_is_rejected(hass) -> None:
    """A final response that disagrees with streamed text must fail explicitly."""
    client = _client()

    async def send_prompt(*_args, on_delta=None, **_kwargs):
        assert on_delta is not None
        on_delta("Streamed text")
        return _prompt_result("Different final text")

    client.async_send_prompt.side_effect = send_prompt
    entity = GitHubCopilotConversationEntity(_entry(client))
    chat_log = _chat_log(hass, "conversation-1", current_user="Hello")

    with patch.object(
        conversation.ChatLog,
        "async_provide_llm_data",
        new=_provide_prompt(DEFAULT_PROMPT),
    ):
        result = await entity._async_handle_message(_input("Hello"), chat_log)

    assert result.response.response_type is intent.IntentResponseType.ERROR


@pytest.mark.asyncio
async def test_communication_error_returns_intent_error(hass) -> None:
    """Provider failures should not look like successful assistant speech."""
    client = _client()
    client.async_send_prompt.side_effect = GitHubCopilotApiClientCommunicationError
    entity = GitHubCopilotConversationEntity(_entry(client))
    chat_log = _chat_log(hass, "conversation-1", current_user="Hello")

    with patch.object(
        conversation.ChatLog,
        "async_provide_llm_data",
        new=_provide_prompt(DEFAULT_PROMPT),
    ):
        result = await entity._async_handle_message(_input("Hello"), chat_log)

    assert result.response.response_type is intent.IntentResponseType.ERROR
    assert len(chat_log.content) == 2
    client.async_end_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_entity_removal_cleans_all_sessions(hass) -> None:
    """Removing the entity should disconnect every active SDK session."""
    client = _client()
    entity = GitHubCopilotConversationEntity(_entry(client))
    entity.hass = hass

    with patch.object(
        conversation.ChatLog,
        "async_provide_llm_data",
        new=_provide_prompt(DEFAULT_PROMPT),
    ):
        await entity._async_handle_message(
            _input("One", "conversation-1"),
            _chat_log(hass, "conversation-1", current_user="One"),
        )
        await entity._async_handle_message(
            _input("Two", "conversation-2"),
            _chat_log(hass, "conversation-2", current_user="Two"),
        )

    with patch.object(
        conversation.ConversationEntity,
        "async_will_remove_from_hass",
        new=AsyncMock(),
    ):
        await entity.async_will_remove_from_hass()

    assert client.async_end_session.await_count == 2


@pytest.mark.asyncio
async def test_expired_session_is_cleaned_by_another_conversation(hass) -> None:
    """Inactive unlocked sessions should be removed before a new turn."""
    client = _client()
    entity = GitHubCopilotConversationEntity(
        _entry(client, {CONF_SESSION_TIMEOUT: 300})
    )

    with (
        patch.object(
            conversation.ChatLog,
            "async_provide_llm_data",
            new=_provide_prompt(DEFAULT_PROMPT),
        ),
        patch(
            "custom_components.github_copilot.conversation.monotonic",
            return_value=1000,
        ),
    ):
        await entity._async_handle_message(
            _input("One", "conversation-1"),
            _chat_log(hass, "conversation-1", current_user="One"),
        )
        entity._sessions["conversation-1"].last_used = 0
        await entity._async_handle_message(
            _input("Two", "conversation-2"),
            _chat_log(hass, "conversation-2", current_user="Two"),
        )

    client.async_end_session.assert_awaited_once_with("sdk-1")


@pytest.mark.asyncio
async def test_same_conversation_recreates_after_inactivity_timeout(hass) -> None:
    """Returning after the timeout should create a fresh SDK session."""
    client = _client()
    entity = GitHubCopilotConversationEntity(
        _entry(client, {CONF_SESSION_TIMEOUT: 300})
    )
    first_log = _chat_log(hass, "conversation-1", current_user="First")
    second_log = _chat_log(
        hass,
        "conversation-1",
        current_user="Second",
        prior=[
            conversation.UserContent(content="First"),
            conversation.AssistantContent(
                agent_id="conversation.github_copilot",
                content="First response",
            ),
        ],
    )

    with (
        patch.object(
            conversation.ChatLog,
            "async_provide_llm_data",
            new=_provide_prompt(DEFAULT_PROMPT),
        ),
        patch(
            "custom_components.github_copilot.conversation.monotonic",
            side_effect=[1000, 1000, 1000, 1000, 1401, 1401, 1401, 1401],
        ),
    ):
        await entity._async_handle_message(_input("First"), first_log)
        await entity._async_handle_message(_input("Second"), second_log)

    assert client.async_create_session.await_count == 2
    client.async_end_session.assert_awaited_once_with("sdk-1")
    assert (
        "Prior conversation transcript"
        in client.async_send_prompt.await_args.kwargs["turn_context"]
    )


def test_transcript_has_strict_character_limit(hass) -> None:
    """A single large prior message should not exceed the transcript budget."""
    chat_log = _chat_log(
        hass,
        "conversation-1",
        current_user="Current",
        prior=[conversation.UserContent(content="x" * 10000)],
    )

    transcript = _bounded_transcript(chat_log)

    assert len(transcript) < 7000
    assert transcript.startswith("Prior conversation transcript")


@pytest.mark.asyncio
async def test_different_conversations_use_different_locks() -> None:
    """Independent conversations should not share serialization locks."""
    entity = GitHubCopilotConversationEntity(_entry(_client()))

    first = await entity._async_get_request_lock("conversation-1")
    second = await entity._async_get_request_lock("conversation-2")

    assert first is not second
