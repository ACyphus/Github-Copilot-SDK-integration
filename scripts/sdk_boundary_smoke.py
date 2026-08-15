#!/usr/bin/env python3
"""Run opt-in live probes against the supported Copilot SDK boundary."""

from __future__ import annotations

import asyncio
import base64
import io
import os
from dataclasses import dataclass, field

from copilot import CopilotClient, RuntimeConnection, ToolSet
from copilot.generated.rpc import (
    PermissionDecisionApproveOnce,
    PermissionDecisionReject,
)
from copilot.session_events import (
    AssistantMessageData,
    AssistantUsageData,
    SessionIdleData,
)
from copilot.tools import Tool, ToolInvocation, ToolResult
from PIL import Image

MODEL = os.environ.get("COPILOT_SMOKE_MODEL", "gpt-5-mini")


@dataclass
class TurnResult:
    """Collect the observable result of one model turn."""

    messages: list[str] = field(default_factory=list)
    usage_events: int = 0


async def _run_turn(
    session,
    prompt: str,
    *,
    attachments: list[dict] | None = None,
) -> TurnResult:
    """Send a message and wait for the session to become idle."""
    result = TurnResult()
    done = asyncio.Event()

    def on_event(event) -> None:
        if isinstance(event.data, AssistantMessageData):
            result.messages.append(event.data.content)
        elif isinstance(event.data, AssistantUsageData):
            result.usage_events += 1
        elif isinstance(event.data, SessionIdleData):
            done.set()

    unsubscribe = session.on(on_event)
    try:
        await session.send(prompt, attachments=attachments)
        await asyncio.wait_for(done.wait(), timeout=120)
    finally:
        unsubscribe()
    return result


async def _echo(invocation: ToolInvocation) -> ToolResult:
    """Return the supplied value to prove a client-side tool round trip."""
    return ToolResult(
        text_result_for_llm=f"echo:{invocation.arguments['value']}",
        session_log="Phase 1 boundary smoke test",
    )


def _permission_handler(request, _invocation):
    """Approve the smoke-test tool and deny every other runtime tool."""
    if getattr(request, "kind", "") == "custom-tool":
        return PermissionDecisionApproveOnce()
    return PermissionDecisionReject(feedback="Denied by the Phase 1 smoke test.")


def _client() -> CopilotClient:
    """Create a local or remote empty-mode SDK client."""
    runtime_url = os.environ.get("COPILOT_RUNTIME_URL", "")
    if runtime_url:
        connection_token = os.environ.get("COPILOT_CONNECTION_TOKEN", "")
        if not connection_token:
            msg = "COPILOT_CONNECTION_TOKEN is required with COPILOT_RUNTIME_URL."
            raise RuntimeError(msg)
        connection = RuntimeConnection.for_uri(
            runtime_url,
            connection_token=connection_token,
        )
        return CopilotClient(connection=connection, log_level="error", mode="empty")
    return CopilotClient(use_logged_in_user=True, log_level="error", mode="empty")


def _image_attachment() -> dict:
    """Create a small synthetic blue image attachment."""
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), (0, 0, 255)).save(buffer, format="PNG")
    return {
        "type": "blob",
        "data": base64.b64encode(buffer.getvalue()).decode(),
        "mimeType": "image/png",
        "displayName": "phase1-blue.png",
    }


async def main() -> None:
    """Run authentication, tool, permission, image, and usage probes."""
    async with _client() as client:
        auth = await client.get_auth_status()
        if not auth.isAuthenticated:
            msg = "Copilot runtime is not authenticated."
            raise RuntimeError(msg)

        models = await client.list_models()
        model_ids = {model.id for model in models}
        if MODEL not in model_ids:
            msg = f"Configured smoke-test model '{MODEL}' is unavailable."
            raise RuntimeError(msg)

        tool = Tool(
            name="phase1_echo",
            description="Echo a value for the Phase 1 boundary test.",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            handler=_echo,
            defer="never",
        )
        async with await client.create_session(
            model=MODEL,
            tools=[tool],
            available_tools=ToolSet().add_custom("phase1_echo"),
            on_permission_request=_permission_handler,
            enable_file_hooks=False,
            enable_host_git_operations=False,
            enable_session_store=False,
            enable_skills=False,
        ) as session:
            tool_result = await _run_turn(
                session,
                "Call phase1_echo with value 'boundary-ok' and report its result.",
            )
            if (
                not tool_result.messages
                or "boundary-ok" not in tool_result.messages[-1]
            ):
                msg = "Custom tool round trip failed."
                raise RuntimeError(msg)
            if tool_result.usage_events == 0:
                msg = "No usage event was emitted for the custom-tool turn."
                raise RuntimeError(msg)

            denied = await _run_turn(
                session,
                "Run the shell command 'echo unsafe'. "
                "If no shell tool exists, say exactly SHELL_BLOCKED.",
            )
            if not denied.messages or denied.messages[-1].strip() != "SHELL_BLOCKED":
                msg = "Built-in shell tool exclusion was not proven."
                raise RuntimeError(msg)

        async with await client.create_session(
            model=MODEL,
            available_tools=[],
            on_permission_request=_permission_handler,
            enable_file_hooks=False,
            enable_host_git_operations=False,
            enable_session_store=False,
            enable_skills=False,
        ) as session:
            image_result = await _run_turn(
                session,
                "State the dominant color of this image in one word.",
                attachments=[_image_attachment()],
            )
            if (
                not image_result.messages
                or "blue" not in image_result.messages[-1].lower()
            ):
                msg = "Image attachment probe failed."
                raise RuntimeError(msg)

    print(
        f"Boundary smoke test passed with {MODEL}: authentication, model listing, "
        "custom tools, default-deny, image input, and usage events."
    )


if __name__ == "__main__":
    asyncio.run(main())
