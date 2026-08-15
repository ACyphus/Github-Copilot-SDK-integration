"""GitHub Copilot SDK client wrapper."""

from __future__ import annotations

import asyncio
import socket
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from copilot.generated.rpc import PermissionDecisionReject

if TYPE_CHECKING:
    from collections.abc import Sequence

    import copilot

try:
    from copilot import CopilotClient, RuntimeConnection
except ImportError:
    CopilotClient = None  # type: ignore[assignment,misc]
    RuntimeConnection = None  # type: ignore[assignment,misc]

_SDK_INSTALL_HINT = (
    "The github-copilot-sdk package is required but is not installed. "
    "Install the integration dependencies again."
)
_SESSION_DISCONNECT_TIMEOUT = 5.0


class GitHubCopilotApiClientError(Exception):
    """Indicate a general Copilot client error."""


class GitHubCopilotApiClientCommunicationError(GitHubCopilotApiClientError):
    """Indicate a Copilot runtime communication error."""


class GitHubCopilotApiClientAuthenticationError(GitHubCopilotApiClientError):
    """Indicate a Copilot authentication error."""


@dataclass
class CopilotSessionContext:
    """Hold an active Copilot SDK session."""

    session_id: str
    copilot_session: copilot.CopilotSession


def _deny_runtime_tool(
    _request: Any,
    _invocation: Any,
) -> PermissionDecisionReject:
    """Deny every runtime tool not explicitly handled by the integration."""
    return PermissionDecisionReject(
        feedback="This Home Assistant session does not permit that tool."
    )


class GitHubCopilotApiClient:
    """Provide the Copilot operations used by Home Assistant."""

    def __init__(
        self,
        model: str = "auto",
        *,
        github_token: str = "",
        cli_url: str = "",
        connection_token: str = "",
        timeout: float = 120.0,
    ) -> None:
        """Initialize the Copilot SDK wrapper."""
        self._model = model
        self._github_token = github_token
        self._cli_url = cli_url.strip()
        self._connection_token = connection_token
        self._timeout = timeout
        self._client: copilot.CopilotClient | None = None
        self._sessions: dict[str, CopilotSessionContext] = {}
        self._session_lock = asyncio.Lock()
        self._client_lock = asyncio.Lock()

    async def async_test_connection(self) -> bool:
        """Validate authentication, model access, and session creation."""
        client = await self._ensure_client()
        models = await self.async_available_models()
        if self._model not in models:
            msg = f"Copilot model '{self._model}' is not available."
            raise GitHubCopilotApiClientError(msg)

        session = await self.async_create_session()
        await self.async_end_session(session.session_id)
        await client.get_auth_status()
        return True

    async def async_create_session(self) -> CopilotSessionContext:
        """Create a tool-isolated Copilot SDK session."""
        async with self._session_lock:
            client = await self._ensure_client()
            try:
                session = await client.create_session(
                    model=self._model,
                    available_tools=[],
                    on_permission_request=_deny_runtime_tool,
                    enable_file_hooks=False,
                    enable_host_git_operations=False,
                    enable_session_store=False,
                    enable_skills=False,
                    streaming=False,
                )
            except TimeoutError as exception:
                msg = f"Timed out creating a Copilot session for '{self._model}'."
                raise GitHubCopilotApiClientCommunicationError(msg) from exception
            except ValueError as exception:
                msg = f"Invalid Copilot session configuration: {exception}"
                raise GitHubCopilotApiClientError(msg) from exception
            except Exception as exception:
                msg = f"Unable to create a Copilot session: {type(exception).__name__}"
                raise GitHubCopilotApiClientError(msg) from exception

            context = CopilotSessionContext(
                session_id=session.session_id,
                copilot_session=session,
            )
            self._sessions[context.session_id] = context
            return context

    async def async_end_session(self, session_id: str) -> None:
        """Disconnect and delete a Copilot session."""
        async with self._session_lock:
            context = self._sessions.pop(session_id, None)
        if context is None:
            return

        client = self._client
        try:
            await asyncio.wait_for(
                context.copilot_session.disconnect(),
                timeout=_SESSION_DISCONNECT_TIMEOUT,
            )
            if client is not None:
                await client.delete_session(session_id)
        except Exception as exception:
            msg = "Unable to clean up the Copilot session."
            raise GitHubCopilotApiClientError(msg) from exception

    async def _evict_broken_session(self, session_id: str) -> None:
        """Remove a failed session without surfacing cleanup errors."""
        async with self._session_lock:
            context = self._sessions.pop(session_id, None)
        if context is None:
            return

        with suppress(Exception):
            await asyncio.wait_for(
                context.copilot_session.disconnect(),
                timeout=_SESSION_DISCONNECT_TIMEOUT,
            )
        if self._client is not None:
            with suppress(Exception):
                await self._client.delete_session(session_id)

    async def async_send_prompt(self, session_id: str, prompt: str) -> str:
        """Send a text prompt to an existing session."""
        if not prompt.strip():
            msg = "Prompt cannot be empty."
            raise GitHubCopilotApiClientError(msg)

        async with self._session_lock:
            context = self._sessions.get(session_id)
        if context is None:
            msg = f"Copilot session '{session_id}' was not found."
            raise GitHubCopilotApiClientError(msg)

        try:
            event = await context.copilot_session.send_and_wait(
                prompt,
                timeout=self._timeout,
            )
        except TimeoutError as exception:
            await self._evict_broken_session(session_id)
            msg = "Timed out waiting for a Copilot response."
            raise GitHubCopilotApiClientCommunicationError(msg) from exception
        except (ConnectionError, OSError) as exception:
            await self._evict_broken_session(session_id)
            msg = "Lost the connection to the Copilot runtime."
            raise GitHubCopilotApiClientCommunicationError(msg) from exception
        except Exception as exception:
            msg = f"Copilot failed to respond: {type(exception).__name__}"
            raise GitHubCopilotApiClientError(msg) from exception

        content = getattr(getattr(event, "data", None), "content", None)
        if not content:
            msg = "Copilot returned an empty response."
            raise GitHubCopilotApiClientError(msg)
        return content

    async def async_close(self) -> None:
        """Close all sessions and stop the SDK client."""
        async with self._session_lock:
            session_ids = list(self._sessions)
        for session_id in session_ids:
            with suppress(GitHubCopilotApiClientError):
                await self.async_end_session(session_id)

        async with self._client_lock:
            if self._client is not None:
                await self._client.stop()
                self._client = None

    async def _ensure_client(self) -> copilot.CopilotClient:
        """Start and authenticate the Copilot SDK client."""
        async with self._client_lock:
            if self._client is not None:
                return self._client
            if CopilotClient is None or RuntimeConnection is None:
                raise GitHubCopilotApiClientError(_SDK_INSTALL_HINT)

            try:
                if self._cli_url:
                    connection = RuntimeConnection.for_uri(
                        self._cli_url,
                        connection_token=self._connection_token,
                    )
                    client = CopilotClient(
                        connection=connection,
                        log_level="error",
                        mode="empty",
                    )
                else:
                    client = CopilotClient(
                        github_token=self._github_token,
                        log_level="error",
                        mode="empty",
                        use_logged_in_user=False,
                    )
                await client.start()
            except Exception as exception:  # noqa: BLE001
                self._raise_start_error(exception)

            try:
                auth_status = await client.get_auth_status()
            except Exception as exception:
                await client.stop()
                msg = "Unable to verify Copilot authentication."
                raise GitHubCopilotApiClientAuthenticationError(msg) from exception

            if not auth_status.isAuthenticated:
                await client.stop()
                msg = (
                    "GitHub Copilot is not authenticated. Configure a supported "
                    "fine-grained token or OAuth user token."
                )
                raise GitHubCopilotApiClientAuthenticationError(msg)

            self._client = client
            return client

    def _raise_start_error(self, exception: Exception) -> None:
        """Translate SDK startup failures without leaking credentials."""
        cause = exception.__cause__ or exception.__context__
        dns_errnos = {
            getattr(socket, "EAI_NONAME", -2),
            getattr(socket, "EAI_AGAIN", -3),
            getattr(socket, "EAI_NODATA", -5),
        }
        if isinstance(cause, OSError) and getattr(cause, "errno", None) in dns_errnos:
            location = self._safe_cli_location()
            msg = f"Cannot resolve the Copilot bridge{location}."
            raise GitHubCopilotApiClientCommunicationError(msg) from exception

        exception_text = str(exception)
        if "AUTHENTICATION_FAILED" in exception_text:
            msg = "The Copilot bridge connection token was rejected."
            raise GitHubCopilotApiClientAuthenticationError(msg) from exception

        msg = f"Unable to connect to the Copilot runtime: {type(exception).__name__}"
        raise GitHubCopilotApiClientCommunicationError(msg) from exception

    def _safe_cli_location(self) -> str:
        """Return a log-safe bridge location."""
        if not self._cli_url:
            return ""
        try:
            parsed = urlparse(
                self._cli_url if "://" in self._cli_url else f"tcp://{self._cli_url}"
            )
            if not parsed.hostname:
                return ""
            host = parsed.hostname
        except ValueError:
            return ""
        return f" at {host}:{parsed.port}" if parsed.port else f" at {host}"

    async def async_available_models(self) -> Sequence[str]:
        """Return model IDs available to the authenticated account."""
        client = await self._ensure_client()
        try:
            models = await client.list_models()
        except Exception as exception:
            msg = "Unable to fetch Copilot models."
            raise GitHubCopilotApiClientCommunicationError(msg) from exception
        return [model.id for model in models]
