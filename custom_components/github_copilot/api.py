"""GitHub Copilot SDK client wrapper."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from copilot.generated.rpc import PermissionDecisionReject
from copilot.session_events import AssistantMessageDeltaData, AssistantUsageData

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import timedelta

    import copilot
    from copilot import Tool
    from copilot.session import BlobAttachment

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
_DEFAULT_SYSTEM_PROMPT = "You are a concise Home Assistant voice agent."
_LOGGER = logging.getLogger(__name__)


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
    model: str = ""


@dataclass(frozen=True, slots=True)
class CopilotModelInfo:
    """Sanitized model capabilities used by Home Assistant."""

    id: str
    name: str
    supports_vision: bool
    supported_media_types: tuple[str, ...]
    max_prompt_images: int | None
    max_prompt_image_size: int | None


@dataclass(frozen=True, slots=True)
class CopilotTurnMetrics:
    """Content-free latency and usage metrics for one Copilot turn."""

    request_duration_ms: int
    time_to_first_token_ms: int | None
    model_duration_ms: int | None
    inter_token_latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    input_characters: int
    response_characters: int
    model: str
    attachment_count: int = 0
    attachment_bytes: int = 0


@dataclass(frozen=True, slots=True)
class CopilotPromptResult:
    """Return Copilot content with timing metadata."""

    content: str
    metrics: CopilotTurnMetrics


@dataclass(frozen=True, slots=True)
class CopilotExecutionResult:
    """Return optional Copilot content with timing metadata."""

    content: str | None
    metrics: CopilotTurnMetrics


def _duration_ms(value: timedelta | None) -> int | None:
    """Convert an optional SDK duration to whole milliseconds."""
    if value is None:
        return None
    return round(value.total_seconds() * 1000)


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

    async def async_create_session(
        self,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
        *,
        model: str | None = None,
        tools: Sequence[Tool] = (),
    ) -> CopilotSessionContext:
        """Create a tool-isolated Copilot SDK session."""
        async with self._session_lock:
            client = await self._ensure_client()
            session_tools = list(tools)
            selected_model = model or self._model
            try:
                session = await client.create_session(
                    model=selected_model,
                    tools=session_tools,
                    available_tools=[tool.name for tool in session_tools],
                    on_permission_request=_deny_runtime_tool,
                    enable_file_hooks=False,
                    enable_host_git_operations=False,
                    enable_config_discovery=False,
                    enable_on_demand_instruction_discovery=False,
                    enable_session_store=False,
                    enable_skills=False,
                    skip_custom_instructions=True,
                    streaming=True,
                    system_message={
                        "mode": "replace",
                        "content": system_prompt,
                    },
                )
            except TimeoutError as exception:
                msg = f"Timed out creating a Copilot session for '{selected_model}'."
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
                model=selected_model,
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

    async def async_send_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        turn_context: str = "",
        on_delta: Callable[[str], None] | None = None,
        attachments: Sequence[BlobAttachment] = (),
    ) -> CopilotPromptResult:
        """Send a text prompt to an existing session."""
        result = await self.async_execute_prompt(
            session_id,
            prompt,
            turn_context=turn_context,
            on_delta=on_delta,
            attachments=attachments,
        )
        if not result.content:
            msg = "Copilot returned an empty response."
            raise GitHubCopilotApiClientError(msg)
        return CopilotPromptResult(content=result.content, metrics=result.metrics)

    async def async_execute_prompt(
        self,
        session_id: str,
        prompt: str,
        *,
        turn_context: str = "",
        on_delta: Callable[[str], None] | None = None,
        attachments: Sequence[BlobAttachment] = (),
    ) -> CopilotExecutionResult:
        """Send a prompt and allow terminal-tool completion without text."""
        if not prompt.strip():
            msg = "Prompt cannot be empty."
            raise GitHubCopilotApiClientError(msg)

        async with self._session_lock:
            context = self._sessions.get(session_id)
        if context is None:
            msg = f"Copilot session '{session_id}' was not found."
            raise GitHubCopilotApiClientError(msg)

        message = f"{turn_context.rstrip()}\n\n{prompt}" if turn_context else prompt
        usage: AssistantUsageData | None = None
        observed_time_to_first_token_ms: int | None = None
        request_started = time.perf_counter()

        def on_event(event: Any) -> None:
            """Capture root-model usage without retaining response content."""
            nonlocal observed_time_to_first_token_ms, usage
            if event.agent_id is not None:
                return
            if isinstance(event.data, AssistantMessageDeltaData):
                if observed_time_to_first_token_ms is None:
                    observed_time_to_first_token_ms = round(
                        (time.perf_counter() - request_started) * 1000
                    )
                if on_delta is not None and event.data.delta_content:
                    on_delta(event.data.delta_content)
            elif isinstance(event.data, AssistantUsageData):
                usage = event.data

        unsubscribe = context.copilot_session.on(on_event)
        try:
            send_kwargs: dict[str, Any] = {"timeout": self._timeout}
            if attachments:
                send_kwargs["attachments"] = list(attachments)
            event = await context.copilot_session.send_and_wait(message, **send_kwargs)
        except asyncio.CancelledError:
            with suppress(Exception):
                await context.copilot_session.abort()
            raise
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
        finally:
            request_duration_ms = round((time.perf_counter() - request_started) * 1000)
            unsubscribe()

        content = getattr(getattr(event, "data", None), "content", None)

        metrics = CopilotTurnMetrics(
            request_duration_ms=request_duration_ms,
            time_to_first_token_ms=(
                _duration_ms(usage.time_to_first_token if usage else None)
                or observed_time_to_first_token_ms
            ),
            model_duration_ms=_duration_ms(usage.duration if usage else None),
            inter_token_latency_ms=_duration_ms(
                usage.inter_token_latency if usage else None
            ),
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            input_characters=len(message),
            response_characters=len(content or ""),
            model=usage.model if usage else context.model or self._model,
            attachment_count=len(attachments),
            attachment_bytes=sum(_blob_attachment_bytes(item) for item in attachments),
        )
        _LOGGER.info(
            (
                "Copilot turn metrics: model=%s request_ms=%d ttft_ms=%s "
                "model_ms=%s inter_token_ms=%s input_tokens=%s output_tokens=%s "
                "input_chars=%d response_chars=%d"
            ),
            metrics.model,
            metrics.request_duration_ms,
            metrics.time_to_first_token_ms,
            metrics.model_duration_ms,
            metrics.inter_token_latency_ms,
            metrics.input_tokens,
            metrics.output_tokens,
            metrics.input_characters,
            metrics.response_characters,
        )
        return CopilotExecutionResult(content=content, metrics=metrics)

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
        return [model.id for model in await self.async_available_model_info()]

    async def async_available_model_info(self) -> Sequence[CopilotModelInfo]:
        """Return sanitized model capability metadata."""
        client = await self._ensure_client()
        try:
            models = await client.list_models()
        except Exception as exception:
            msg = "Unable to fetch Copilot models."
            raise GitHubCopilotApiClientCommunicationError(msg) from exception
        return [_sanitize_model_info(model) for model in models]

    async def async_get_model_info(self, model_id: str) -> CopilotModelInfo:
        """Return capability metadata for one available model."""
        for model in await self.async_available_model_info():
            if model.id == model_id:
                return model
        msg = f"Copilot model '{model_id}' is not available."
        raise GitHubCopilotApiClientError(msg)


def _sanitize_model_info(model: Any) -> CopilotModelInfo:
    """Convert SDK model metadata to the integration's safe subset."""
    capabilities = getattr(model, "capabilities", None)
    supports = getattr(capabilities, "supports", None)
    limits = getattr(capabilities, "limits", None)
    vision_limits = getattr(limits, "vision", None)
    media_types = getattr(vision_limits, "supported_media_types", None) or ()
    return CopilotModelInfo(
        id=str(model.id),
        name=str(getattr(model, "name", model.id)),
        supports_vision=bool(getattr(supports, "vision", False)),
        supported_media_types=tuple(str(item) for item in media_types),
        max_prompt_images=getattr(vision_limits, "max_prompt_images", None),
        max_prompt_image_size=getattr(vision_limits, "max_prompt_image_size", None),
    )


def _blob_attachment_bytes(attachment: BlobAttachment) -> int:
    """Estimate decoded blob size from base64 length without decoding."""
    data = attachment["data"]
    padding = len(data) - len(data.rstrip("="))
    return max(0, (len(data) * 3) // 4 - padding)
