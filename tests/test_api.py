"""Tests for the Copilot SDK wrapper."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from copilot.session_events import AssistantMessageDeltaData, AssistantUsageData

from custom_components.github_copilot.api import (
    GitHubCopilotApiClient,
    GitHubCopilotApiClientAuthenticationError,
)


def _mock_sdk_client() -> MagicMock:
    client = MagicMock()
    client.start = AsyncMock()
    client.stop = AsyncMock()
    client.get_auth_status = AsyncMock(
        return_value=SimpleNamespace(isAuthenticated=True)
    )
    client.list_models = AsyncMock(
        return_value=[SimpleNamespace(id="auto"), SimpleNamespace(id="gpt-5-mini")]
    )
    return client


@pytest.mark.asyncio
async def test_remote_client_uses_authenticated_empty_runtime() -> None:
    """Remote mode should use an authenticated URI connection in empty mode."""
    sdk_client = _mock_sdk_client()
    connection = object()

    with (
        patch(
            "custom_components.github_copilot.api.RuntimeConnection.for_uri",
            return_value=connection,
        ) as for_uri,
        patch(
            "custom_components.github_copilot.api.CopilotClient",
            return_value=sdk_client,
        ) as client_factory,
    ):
        client = GitHubCopilotApiClient(
            cli_url="bridge:8000",
            connection_token="bridge-secret",
        )
        await client.async_available_models()

    for_uri.assert_called_once_with(
        "bridge:8000",
        connection_token="bridge-secret",
    )
    client_factory.assert_called_once_with(
        connection=connection,
        log_level="error",
        mode="empty",
    )


@pytest.mark.asyncio
async def test_local_client_uses_only_explicit_github_token() -> None:
    """Local mode should disable ambient account authentication."""
    sdk_client = _mock_sdk_client()

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=sdk_client,
    ) as client_factory:
        client = GitHubCopilotApiClient(github_token="github-token")
        await client.async_available_models()

    client_factory.assert_called_once_with(
        github_token="github-token",
        log_level="error",
        mode="empty",
        use_logged_in_user=False,
    )


@pytest.mark.asyncio
async def test_unauthenticated_client_is_rejected() -> None:
    """An unauthenticated runtime should fail explicitly."""
    sdk_client = _mock_sdk_client()
    sdk_client.get_auth_status.return_value = SimpleNamespace(isAuthenticated=False)

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=sdk_client,
    ):
        client = GitHubCopilotApiClient(github_token="invalid")
        with pytest.raises(GitHubCopilotApiClientAuthenticationError):
            await client.async_available_models()

    sdk_client.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_rejected_bridge_token_is_authentication_error() -> None:
    """A JSON-RPC connection-token failure should be categorized as auth."""
    sdk_client = _mock_sdk_client()
    sdk_client.start.side_effect = RuntimeError("AUTHENTICATION_FAILED")

    with (
        patch(
            "custom_components.github_copilot.api.RuntimeConnection.for_uri",
            return_value=object(),
        ),
        patch(
            "custom_components.github_copilot.api.CopilotClient",
            return_value=sdk_client,
        ),
    ):
        client = GitHubCopilotApiClient(
            cli_url="bridge:8000",
            connection_token="wrong-secret",
        )
        with pytest.raises(GitHubCopilotApiClientAuthenticationError):
            await client.async_available_models()


@pytest.mark.asyncio
async def test_session_disables_ambient_tools() -> None:
    """Created sessions should start without built-in runtime tools."""
    sdk_client = _mock_sdk_client()
    session = MagicMock(session_id="session-id")
    sdk_client.create_session = AsyncMock(return_value=session)

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=sdk_client,
    ):
        client = GitHubCopilotApiClient(github_token="github-token")
        await client.async_create_session("Home Assistant system prompt")

    call = sdk_client.create_session.await_args
    assert call.kwargs["tools"] == []
    assert call.kwargs["available_tools"] == []
    assert call.kwargs["system_message"] == {
        "mode": "replace",
        "content": "Home Assistant system prompt",
    }
    assert call.kwargs["skip_custom_instructions"] is True
    assert call.kwargs["enable_config_discovery"] is False
    assert call.kwargs["enable_on_demand_instruction_discovery"] is False
    assert call.kwargs["enable_file_hooks"] is False
    assert call.kwargs["enable_host_git_operations"] is False
    assert call.kwargs["enable_session_store"] is False
    assert call.kwargs["enable_skills"] is False
    assert call.kwargs["streaming"] is True


@pytest.mark.asyncio
async def test_session_cleanup_disconnects_and_deletes() -> None:
    """Session cleanup should release both client and runtime state."""
    sdk_client = _mock_sdk_client()
    session = MagicMock(session_id="session-id")
    session.disconnect = AsyncMock()
    sdk_client.create_session = AsyncMock(return_value=session)
    sdk_client.delete_session = AsyncMock()

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=sdk_client,
    ):
        client = GitHubCopilotApiClient(github_token="github-token")
        context = await client.async_create_session()
        await client.async_end_session(context.session_id)

    session.disconnect.assert_awaited_once()
    sdk_client.delete_session.assert_awaited_once_with("session-id")


@pytest.mark.asyncio
async def test_turn_context_is_prepended_to_prompt() -> None:
    """Dynamic Home Assistant context should be sent with the current turn."""
    sdk_client = _mock_sdk_client()
    sdk_session = MagicMock(session_id="session-id")
    sdk_session.on.return_value = MagicMock()
    sdk_session.send_and_wait = AsyncMock(
        return_value=SimpleNamespace(data=SimpleNamespace(content="response"))
    )
    sdk_client.create_session = AsyncMock(return_value=sdk_session)

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=sdk_client,
    ):
        client = GitHubCopilotApiClient(github_token="github-token")
        context = await client.async_create_session()
        result = await client.async_send_prompt(
            context.session_id,
            "What time is it?",
            turn_context="Current time is 10:00:00.",
        )

    assert result.content == "response"
    assert result.metrics.input_characters == len(
        "Current time is 10:00:00.\n\nWhat time is it?"
    )
    assert result.metrics.response_characters == len("response")
    assert result.metrics.time_to_first_token_ms is None
    sdk_session.send_and_wait.assert_awaited_once_with(
        "Current time is 10:00:00.\n\nWhat time is it?",
        timeout=120.0,
    )
    sdk_session.on.return_value.assert_called_once_with()


@pytest.mark.asyncio
async def test_turn_captures_content_free_usage_metrics() -> None:
    """SDK usage events should become content-free turn metrics."""
    sdk_client = _mock_sdk_client()
    sdk_session = MagicMock(session_id="session-id")
    handler = None

    def register(callback):
        nonlocal handler
        handler = callback
        return MagicMock()

    async def send_and_wait(*_args, **_kwargs):
        assert handler is not None
        handler(
            SimpleNamespace(
                agent_id=None,
                data=AssistantUsageData(
                    model="gpt-5-mini",
                    duration=timedelta(milliseconds=900),
                    inter_token_latency=timedelta(milliseconds=24),
                    input_tokens=120,
                    output_tokens=18,
                    time_to_first_token=timedelta(milliseconds=310),
                ),
            )
        )
        return SimpleNamespace(data=SimpleNamespace(content="response"))

    sdk_session.on.side_effect = register
    sdk_session.send_and_wait = AsyncMock(side_effect=send_and_wait)
    sdk_client.create_session = AsyncMock(return_value=sdk_session)

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=sdk_client,
    ):
        client = GitHubCopilotApiClient(github_token="github-token")
        context = await client.async_create_session()
        result = await client.async_send_prompt(context.session_id, "Hello")

    assert result.metrics.model == "gpt-5-mini"
    assert result.metrics.time_to_first_token_ms == 310
    assert result.metrics.model_duration_ms == 900
    assert result.metrics.inter_token_latency_ms == 24
    assert result.metrics.input_tokens == 120
    assert result.metrics.output_tokens == 18


@pytest.mark.asyncio
async def test_turn_forwards_root_assistant_deltas() -> None:
    """Only root assistant message deltas should be forwarded."""
    sdk_client = _mock_sdk_client()
    sdk_session = MagicMock(session_id="session-id")
    handler = None

    def register(callback):
        nonlocal handler
        handler = callback
        return MagicMock()

    async def send_and_wait(*_args, **_kwargs):
        assert handler is not None
        handler(
            SimpleNamespace(
                agent_id="subagent-id",
                data=AssistantMessageDeltaData(
                    delta_content="ignored",
                    message_id="subagent-message",
                ),
            )
        )
        handler(
            SimpleNamespace(
                agent_id=None,
                data=AssistantMessageDeltaData(
                    delta_content="streamed ",
                    message_id="message-id",
                ),
            )
        )
        handler(
            SimpleNamespace(
                agent_id=None,
                data=AssistantMessageDeltaData(
                    delta_content="response",
                    message_id="message-id",
                ),
            )
        )
        return SimpleNamespace(data=SimpleNamespace(content="streamed response"))

    sdk_session.on.side_effect = register
    sdk_session.send_and_wait = AsyncMock(side_effect=send_and_wait)
    sdk_client.create_session = AsyncMock(return_value=sdk_session)
    deltas = []

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=sdk_client,
    ):
        client = GitHubCopilotApiClient(github_token="github-token")
        context = await client.async_create_session()
        result = await client.async_send_prompt(
            context.session_id,
            "Hello",
            on_delta=deltas.append,
        )

    assert deltas == ["streamed ", "response"]
    assert result.content == "streamed response"
    assert result.metrics.time_to_first_token_ms is not None


@pytest.mark.asyncio
async def test_cancelled_turn_aborts_sdk_session() -> None:
    """Cancelling a turn should abort model generation and keep the session valid."""
    sdk_client = _mock_sdk_client()
    sdk_session = MagicMock(session_id="session-id")
    sdk_session.on.return_value = MagicMock()
    sdk_session.abort = AsyncMock()
    waiting = asyncio.Event()

    async def send_and_wait(*_args, **_kwargs):
        waiting.set()
        await asyncio.Event().wait()

    sdk_session.send_and_wait = AsyncMock(side_effect=send_and_wait)
    sdk_client.create_session = AsyncMock(return_value=sdk_session)

    with patch(
        "custom_components.github_copilot.api.CopilotClient",
        return_value=sdk_client,
    ):
        client = GitHubCopilotApiClient(github_token="github-token")
        context = await client.async_create_session()
        task = asyncio.create_task(
            client.async_send_prompt(context.session_id, "Write a long response")
        )
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    sdk_session.abort.assert_awaited_once_with()
