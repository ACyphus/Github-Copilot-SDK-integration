"""Tests for the Copilot SDK wrapper."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        await client.async_create_session()

    call = sdk_client.create_session.await_args
    assert call.kwargs["available_tools"] == []
    assert call.kwargs["enable_file_hooks"] is False
    assert call.kwargs["enable_host_git_operations"] is False
    assert call.kwargs["enable_session_store"] is False
    assert call.kwargs["enable_skills"] is False


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
