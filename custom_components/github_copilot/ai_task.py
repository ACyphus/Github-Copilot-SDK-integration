"""AI Task platform for GitHub Copilot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

import voluptuous as vol
from copilot import Tool, ToolInvocation, ToolResult
from homeassistant.components import ai_task, conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from voluptuous_openapi import convert

from .api import (
    CopilotExecutionResult,
    CopilotPromptResult,
    CopilotSessionContext,
    GitHubCopilotApiClientAuthenticationError,
    GitHubCopilotApiClientCommunicationError,
    GitHubCopilotApiClientError,
)
from .attachments import PreparedAttachments, async_prepare_attachments
from .const import CONF_AI_TASK_MODEL, CONF_MODEL, DEFAULT_MODEL, LOGGER

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from .data import GitHubCopilotConfigEntry

_PLAIN_SYSTEM_PROMPT = """
Complete the supplied Home Assistant AI Task using only the data included in
the task instructions. Return concise plain text. Do not claim access to Home
Assistant state, history, controls, files, tools, or external systems. Do not
use Markdown or URLs unless the task explicitly requests them.
""".strip()

_STRUCTURED_SYSTEM_PROMPT = """
Complete the supplied Home Assistant AI Task using only the data included in
the task instructions. Submit the final result by calling submit_result exactly
once. Do not put the structured result in assistant prose. Include every
required field, omit fields absent from the schema, and use validation feedback
to repair one invalid submission.
""".strip()

_VISION_SYSTEM_PROMPT = """
Analyze only the explicitly attached still image or images. Distinguish visible
evidence from uncertainty. Do not claim continuous video awareness. Do not
identify people, infer identity, emotion, protected traits, or threat level.
Do not invent details that are not visibly supported.
""".strip()


async def async_setup_entry(
    _hass: HomeAssistant,
    config_entry: GitHubCopilotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the GitHub Copilot AI Task platform."""
    async_add_entities([GitHubCopilotAITaskEntity(config_entry)])


@dataclass(slots=True)
class StructuredResultHolder:
    """Capture and validate one structured AI Task result."""

    chat_log: conversation.ChatLog
    agent_id: str
    structure: vol.Schema
    submissions: int = 0
    value: dict[str, Any] | None = None
    terminal_invalid: bool = False

    async def async_submit(self, invocation: ToolInvocation) -> ToolResult:
        """Validate one submit_result invocation."""
        self.submissions += 1
        arguments = invocation.arguments
        error_text: str | None = None
        validated: Any = None

        if not isinstance(arguments, dict):
            error_text = "The submitted result must be a JSON object."
            recorded_arguments: dict[str, Any] = {}
        else:
            recorded_arguments = arguments
            try:
                validated = self.structure(arguments)
            except vol.Invalid as err:
                error_text = _validation_error_text(err)

        self.chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=self.agent_id,
                tool_calls=[
                    llm.ToolInput(
                        tool_name="submit_result",
                        tool_args=recorded_arguments,
                        id=invocation.tool_call_id,
                        external=True,
                    )
                ],
            )
        )

        if error_text is None:
            self.value = validated
            tool_result: dict[str, Any] = {"success": True}
            result_type = "success"
            result_text = "The structured result was accepted."
        else:
            tool_result = {
                "success": False,
                "error": "SchemaValidationError",
                "error_text": error_text,
            }
            if self.submissions == 1:
                result_type = "failure"
                result_text = f"Invalid structured result: {error_text}"
            else:
                self.terminal_invalid = True
                result_type = "success"
                result_text = (
                    "The structured result was rejected after the single repair "
                    "attempt. Stop without retrying."
                )

        self.chat_log.async_add_assistant_content_without_tools(
            conversation.ToolResultContent(
                agent_id=self.agent_id,
                tool_call_id=invocation.tool_call_id,
                tool_name="submit_result",
                tool_result=tool_result,
            )
        )
        return ToolResult(
            text_result_for_llm=result_text,
            result_type=result_type,
        )


class GitHubCopilotAITaskEntity(ai_task.AITaskEntity):
    """Generate stateless AI Task data with GitHub Copilot."""

    _attr_has_entity_name = True
    _attr_name = "GitHub Copilot"
    _attr_supported_features = (
        ai_task.AITaskEntityFeature.GENERATE_DATA
        | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
    )

    def __init__(self, config_entry: GitHubCopilotConfigEntry) -> None:
        """Initialize the AI Task entity."""
        self.entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}-ai-task"
        self._attr_extra_state_attributes = {}

    @override
    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Generate plain text or schema-validated data."""
        if chat_log.llm_api is not None:
            msg = "GitHub Copilot AI Tasks do not accept Home Assistant tools."
            raise HomeAssistantError(msg)

        system_prompt, user_prompt = _task_prompts(
            chat_log,
            structured=bool(task.structure),
        )
        model = self.entry.data.get(
            CONF_AI_TASK_MODEL,
            self.entry.data.get(CONF_MODEL, DEFAULT_MODEL),
        )
        prepared = PreparedAttachments((), 0, 0)
        if task.attachments:
            try:
                model_info = await self.entry.runtime_data.client.async_get_model_info(
                    model
                )
            except GitHubCopilotApiClientError as err:
                msg = "The configured AI Task model is unavailable for vision."
                raise HomeAssistantError(msg) from err
            prepared = await async_prepare_attachments(
                self.hass,
                task.attachments,
                model_info,
                self.entry.data,
            )
            system_prompt = (
                f"{system_prompt}\n{_VISION_SYSTEM_PROMPT}\n"
                f"{len(prepared.blobs)} image attachment(s) are supplied."
            )
        holder: StructuredResultHolder | None = None
        tools: list[Tool] = []
        if task.structure is not None:
            schema = convert(
                task.structure,
                custom_serializer=llm.selector_serializer,
            )
            holder = StructuredResultHolder(
                chat_log=chat_log,
                agent_id=self.entity_id or "ai_task.github_copilot",
                structure=task.structure,
            )
            tools = [
                Tool(
                    name="submit_result",
                    description=(
                        f"Submit the final schema-validated result for task "
                        f"'{task.name}'."
                    ),
                    parameters=schema,
                    handler=holder.async_submit,
                    skip_permission=True,
                    defer="never",
                    is_terminal=True,
                )
            ]

        session: CopilotSessionContext | None = None
        try:
            session = await self.entry.runtime_data.client.async_create_session(
                system_prompt,
                model=model,
                tools=tools,
            )
            if holder is None:
                prompt_result = await self.entry.runtime_data.client.async_send_prompt(
                    session.session_id,
                    user_prompt,
                    attachments=prepared.blobs,
                )
                chat_log.async_add_assistant_content_without_tools(
                    conversation.AssistantContent(
                        agent_id=self.entity_id or "ai_task.github_copilot",
                        content=prompt_result.content,
                    )
                )
                self._set_metrics(prompt_result, prepared)
                return ai_task.GenDataTaskResult(
                    conversation_id=chat_log.conversation_id,
                    data=prompt_result.content,
                )

            execution_result = (
                await self.entry.runtime_data.client.async_execute_prompt(
                    session.session_id,
                    user_prompt,
                    attachments=prepared.blobs,
                )
            )
            self._set_metrics(execution_result, prepared)
            if holder.value is not None:
                return ai_task.GenDataTaskResult(
                    conversation_id=chat_log.conversation_id,
                    data=holder.value,
                )
            if holder.terminal_invalid:
                msg = "Structured result failed validation after one repair attempt."
                raise HomeAssistantError(msg)
            msg = "Copilot did not submit a structured result."
            raise HomeAssistantError(msg)
        except GitHubCopilotApiClientAuthenticationError as err:
            msg = "GitHub Copilot authentication failed."
            raise HomeAssistantError(msg) from err
        except GitHubCopilotApiClientCommunicationError as err:
            msg = "GitHub Copilot could not be reached."
            raise HomeAssistantError(msg) from err
        except GitHubCopilotApiClientError as err:
            msg = "GitHub Copilot could not complete the AI Task."
            raise HomeAssistantError(msg) from err
        finally:
            if session is not None:
                try:
                    await self.entry.runtime_data.client.async_end_session(
                        session.session_id
                    )
                except GitHubCopilotApiClientError:
                    LOGGER.warning("AI Task Copilot session cleanup failed")

    def _set_metrics(
        self,
        result: CopilotExecutionResult | CopilotPromptResult,
        attachments: PreparedAttachments,
    ) -> None:
        """Publish sanitized metrics for the most recent task."""
        metrics = result.metrics
        self._attr_extra_state_attributes = {
            "model": metrics.model,
            "request_duration_ms": metrics.request_duration_ms,
            "time_to_first_token_ms": metrics.time_to_first_token_ms,
            "model_duration_ms": metrics.model_duration_ms,
            "input_tokens": metrics.input_tokens,
            "output_tokens": metrics.output_tokens,
            "input_characters": metrics.input_characters,
            "response_characters": metrics.response_characters,
            "attachment_count": len(attachments.blobs),
            "attachment_original_bytes": attachments.original_bytes,
            "attachment_normalized_bytes": attachments.normalized_bytes,
        }
        if self.hass is not None and self.entity_id is not None:
            self.async_write_ha_state()


def _task_prompts(
    chat_log: conversation.ChatLog,
    *,
    structured: bool,
) -> tuple[str, str]:
    """Return AI Task system and user prompts from Home Assistant ChatLog."""
    system_content = chat_log.content[0]
    user_content = chat_log.content[-1]
    if not isinstance(system_content, conversation.SystemContent) or not isinstance(
        user_content,
        conversation.UserContent,
    ):
        msg = "Home Assistant did not prepare a valid AI Task prompt."
        raise HomeAssistantError(msg)
    task_prompt = _STRUCTURED_SYSTEM_PROMPT if structured else _PLAIN_SYSTEM_PROMPT
    return f"{system_content.content}\n{task_prompt}", user_content.content


def _validation_error_text(err: vol.Invalid) -> str:
    """Return concise field-level validation feedback."""
    path = ".".join(str(part) for part in err.path)
    return f"{path}: {err.msg}" if path else err.msg
