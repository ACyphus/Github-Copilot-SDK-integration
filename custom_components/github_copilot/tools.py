"""Translate and execute Home Assistant LLM tools."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from copilot import Tool as CopilotTool
from copilot import ToolInvocation, ToolResult
from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_should_expose
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder import history as recorder_history
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import intent, llm
from homeassistant.helpers.json import json_dumps
from homeassistant.util import dt as dt_util
from sqlalchemy.exc import SQLAlchemyError
from voluptuous_openapi import convert

from .const import LOGGER

if TYPE_CHECKING:
    from collections.abc import Sequence

    from homeassistant.core import HomeAssistant

_TOOL_TIMEOUT_SECONDS = 30
_MAX_HISTORY_ENTITIES = 5
_SENSITIVE_COVER_CLASSES = {"door", "garage", "gate"}
_HISTORY_PROMPT = """
Use the GetHistory tool for questions about past states, trends, durations,
minimums, maximums, averages, or phrases such as "over the last 24 hours".
Prefer area and device_class filters when the user describes a measurement by
location, such as garage temperature. GetHistory can only read entities exposed
to this assistant and returns bounded summaries rather than raw history.
""".strip()


@dataclass(frozen=True, slots=True)
class CopilotToolSpec:
    """Serializable Copilot custom-tool definition."""

    name: str
    description: str
    parameters: dict[str, Any]


class GetHistoryTool(llm.Tool):
    """Return bounded Recorder summaries for exposed entities."""

    name = "GetHistory"
    description = (
        "Summarize historical Home Assistant states for exposed entities. "
        "Use for past values, trends, min/max/average, durations, or questions "
        "covering a time period. Filter by entity_id or by name, area, domain, "
        "and device_class. Returns summaries only, never raw history."
    )
    parameters = vol.Schema(
        {
            vol.Optional("entity_id"): cv.entity_id,
            vol.Optional("name"): cv.string,
            vol.Optional("area"): cv.string,
            vol.Optional("domain"): vol.Any(cv.string, [cv.string]),
            vol.Optional("device_class"): vol.Any(cv.string, [cv.string]),
            vol.Optional("hours", default=24): vol.All(
                vol.Coerce(int),
                vol.Range(min=1, max=168),
            ),
        }
    )

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        """Query Recorder and return deterministic summaries."""
        arguments = self.parameters(tool_input.tool_args)
        entity_ids = _resolve_history_entity_ids(
            hass,
            llm_context,
            arguments,
        )
        if not entity_ids:
            return {
                "success": False,
                "error": "No exposed entities matched the history filters.",
            }
        if len(entity_ids) > _MAX_HISTORY_ENTITIES:
            return {
                "success": False,
                "error": (
                    f"History matched {len(entity_ids)} entities. Narrow the "
                    f"request to {_MAX_HISTORY_ENTITIES} or fewer."
                ),
            }

        end_time = dt_util.utcnow()
        start_time = end_time - timedelta(hours=arguments["hours"])
        try:
            instance = get_instance(hass)
            history = await instance.async_add_executor_job(
                functools.partial(
                    recorder_history.get_significant_states,
                    hass,
                    start_time,
                    end_time=end_time,
                    entity_ids=entity_ids,
                    include_start_time_state=True,
                    significant_changes_only=False,
                    minimal_response=False,
                    no_attributes=True,
                )
            )
        except (KeyError, SQLAlchemyError) as err:
            msg = "Home Assistant history is unavailable."
            raise HomeAssistantError(msg) from err

        summaries = [
            _summarize_history(
                hass.states.get(entity_id),
                history.get(entity_id, []),
                (start_time, end_time),
            )
            for entity_id in entity_ids
        ]
        return {
            "success": True,
            "period": {
                "start": dt_util.as_local(start_time).isoformat(),
                "end": dt_util.as_local(end_time).isoformat(),
                "hours": arguments["hours"],
            },
            "entities": summaries,
        }


def add_history_tool(chat_log: conversation.ChatLog) -> None:
    """Add GetHistory and its usage prompt to a prepared ChatLog."""
    if chat_log.llm_api is None:
        return
    if not any(tool.name == GetHistoryTool.name for tool in chat_log.llm_api.tools):
        chat_log.llm_api.tools.append(GetHistoryTool())
    system_content = chat_log.content[0]
    if (
        isinstance(system_content, conversation.SystemContent)
        and _HISTORY_PROMPT not in system_content.content
    ):
        chat_log.content[0] = conversation.SystemContent(
            content=f"{system_content.content}\n{_HISTORY_PROMPT}"
        )


def _resolve_history_entity_ids(
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
    arguments: dict[str, Any],
) -> list[str]:
    """Resolve only entities exposed to the active assistant."""
    if entity_id := arguments.get("entity_id"):
        if hass.states.get(entity_id) is None or not async_should_expose(
            hass,
            llm_context.assistant,
            entity_id,
        ):
            return []
        return [entity_id]

    domains = arguments.get("domain")
    if isinstance(domains, str):
        domains = [domains]
    device_classes = arguments.get("device_class")
    if isinstance(device_classes, str):
        device_classes = [device_classes]
    name = arguments.get("name")
    area = arguments.get("area")
    if not any((name, area, domains, device_classes)):
        return []

    result = intent.async_match_targets(
        hass,
        intent.MatchTargetsConstraints(
            name=name,
            area_name=area,
            domains=domains,
            device_classes=device_classes,
            assistant=llm_context.assistant,
            allow_duplicate_names=True,
        ),
    )
    if not result.is_match:
        return []
    return sorted(state.entity_id for state in result.states)


def _summarize_history(
    current_state: State | None,
    raw_states: list[State | dict[str, Any]],
    period: tuple[datetime, datetime],
) -> dict[str, Any]:
    """Summarize one entity's history without returning raw samples."""
    states = sorted(
        (state for state in raw_states if isinstance(state, State)),
        key=lambda state: state.last_updated,
    )
    entity_id = (
        current_state.entity_id
        if current_state is not None
        else states[0].entity_id
        if states
        else "unknown"
    )
    summary: dict[str, Any] = {
        "entity_id": entity_id,
        "name": current_state.name if current_state is not None else entity_id,
        "samples": len(states),
    }
    if not states:
        summary["available"] = False
        return summary

    summary.update(
        {
            "available": True,
            "start_state": states[0].state,
            "end_state": states[-1].state,
            "current_state": current_state.state if current_state else states[-1].state,
        }
    )
    numeric_states = [
        (state, value)
        for state in states
        if (value := _finite_float(state.state)) is not None
    ]
    if numeric_states and _finite_float(states[-1].state) is not None:
        _add_numeric_summary(
            summary,
            states,
            numeric_states,
            period,
            current_state,
        )
    else:
        _add_state_duration_summary(summary, states, period)
    return summary


def _add_numeric_summary(
    summary: dict[str, Any],
    states: list[State],
    numeric_states: list[tuple[State, float]],
    period: tuple[datetime, datetime],
    current_state: State | None,
) -> None:
    """Add numeric statistics, including a time-weighted average."""
    start_time, end_time = period
    weighted_total = 0.0
    weighted_seconds = 0.0
    for index, state in enumerate(states):
        value = _finite_float(state.state)
        if value is None:
            continue
        segment_start = max(state.last_updated, start_time)
        segment_end = (
            min(states[index + 1].last_updated, end_time)
            if index + 1 < len(states)
            else end_time
        )
        seconds = max(0.0, (segment_end - segment_start).total_seconds())
        weighted_total += value * seconds
        weighted_seconds += seconds

    values = [value for _state, value in numeric_states]
    minimum_state, minimum = min(numeric_states, key=lambda item: item[1])
    maximum_state, maximum = max(numeric_states, key=lambda item: item[1])
    average = (
        weighted_total / weighted_seconds
        if weighted_seconds
        else sum(values) / len(values)
    )
    start_value = numeric_states[0][1]
    end_value = numeric_states[-1][1]
    summary["numeric"] = {
        "average": round(average, 3),
        "minimum": round(minimum, 3),
        "minimum_at": dt_util.as_local(minimum_state.last_updated).isoformat(),
        "maximum": round(maximum, 3),
        "maximum_at": dt_util.as_local(maximum_state.last_updated).isoformat(),
        "start": round(start_value, 3),
        "end": round(end_value, 3),
        "change": round(end_value - start_value, 3),
    }
    if current_state and (unit := current_state.attributes.get("unit_of_measurement")):
        summary["unit_of_measurement"] = unit


def _add_state_duration_summary(
    summary: dict[str, Any],
    states: list[State],
    period: tuple[datetime, datetime],
) -> None:
    """Add duration and transition summaries for non-numeric entities."""
    start_time, end_time = period
    durations: dict[str, float] = {}
    transitions = 0
    previous_state: str | None = None
    for index, state in enumerate(states):
        segment_start = max(state.last_updated, start_time)
        segment_end = (
            min(states[index + 1].last_updated, end_time)
            if index + 1 < len(states)
            else end_time
        )
        duration = max(0.0, (segment_end - segment_start).total_seconds())
        durations[state.state] = durations.get(state.state, 0.0) + duration
        if previous_state is not None and state.state != previous_state:
            transitions += 1
        previous_state = state.state
    summary["state_durations_hours"] = {
        state: round(seconds / 3600, 3) for state, seconds in durations.items()
    }
    summary["state_changes"] = transitions


def _finite_float(value: str) -> float | None:
    """Parse a finite numeric state."""
    try:
        parsed = float(value)
    except TypeError, ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def prepare_tool_specs(
    api_instance: llm.APIInstance | None,
) -> tuple[list[CopilotToolSpec], str]:
    """Convert Home Assistant tools and return a stable fingerprint."""
    if api_instance is None:
        return [], hashlib.sha256(b"[]").hexdigest()

    serializer = api_instance.custom_serializer or llm.selector_serializer
    specs = [
        CopilotToolSpec(
            name=tool.name,
            description=tool.description or "",
            parameters=convert(tool.parameters, custom_serializer=serializer),
        )
        for tool in api_instance.tools
    ]
    names = [spec.name for spec in specs]
    if len(names) != len(set(names)):
        msg = "Home Assistant provided duplicate LLM tool names."
        raise ValueError(msg)

    serialized = json.dumps(
        [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            }
            for spec in specs
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return specs, hashlib.sha256(serialized.encode()).hexdigest()


def create_copilot_tools(
    specs: Sequence[CopilotToolSpec],
    dispatcher: HomeAssistantToolDispatcher,
) -> list[CopilotTool]:
    """Create SDK tools backed by one mutable Home Assistant dispatcher."""
    return [
        CopilotTool(
            name=spec.name,
            description=spec.description,
            parameters=spec.parameters,
            handler=dispatcher.async_invoke,
            skip_permission=True,
            defer="never",
        )
        for spec in specs
    ]


class HomeAssistantToolDispatcher:
    """Bind SDK tool calls to the current Home Assistant conversation turn."""

    def __init__(self, max_calls: int) -> None:
        """Initialize the dispatcher."""
        self._max_calls = max_calls
        self._execution_lock = asyncio.Lock()
        self._chat_log: conversation.ChatLog | None = None
        self._agent_id = ""
        self._call_count = 0

    def bind(
        self,
        chat_log: conversation.ChatLog,
        agent_id: str,
        max_calls: int,
    ) -> None:
        """Bind this session dispatcher to the current serialized turn."""
        self._chat_log = chat_log
        self._agent_id = agent_id
        self._max_calls = max_calls
        self._call_count = 0

    async def async_invoke(  # noqa: PLR0911
        self,
        invocation: ToolInvocation,
    ) -> ToolResult:
        """Execute one SDK custom-tool request through Home Assistant."""
        async with self._execution_lock:
            chat_log = self._chat_log
            if chat_log is None or chat_log.llm_api is None:
                return ToolResult(
                    text_result_for_llm="Home Assistant tools are unavailable.",
                    result_type="failure",
                )

            self._call_count += 1
            arguments = invocation.arguments
            if not isinstance(arguments, dict):
                return await self._async_record_failure(
                    invocation,
                    "Tool arguments must be a JSON object.",
                )
            if self._call_count > self._max_calls:
                return await self._async_record_failure(
                    invocation,
                    "The Home Assistant tool-call limit was reached.",
                )
            if rejection := self._security_rejection(
                chat_log,
                invocation.tool_name,
                arguments,
            ):
                return await self._async_record_failure(invocation, rejection)

            tool_input = llm.ToolInput(
                tool_name=invocation.tool_name,
                tool_args=arguments,
                id=invocation.tool_call_id,
            )
            assistant_content = conversation.AssistantContent(
                agent_id=self._agent_id,
                tool_calls=[tool_input],
            )
            try:
                async with asyncio.timeout(_TOOL_TIMEOUT_SECONDS):
                    results = [
                        result
                        async for result in chat_log.async_add_assistant_content(
                            assistant_content
                        )
                    ]
            except TimeoutError:
                chat_log.async_add_assistant_content_without_tools(
                    conversation.ToolResultContent(
                        agent_id=self._agent_id,
                        tool_call_id=invocation.tool_call_id,
                        tool_name=invocation.tool_name,
                        tool_result={
                            "error": "ToolTimeout",
                            "error_text": (
                                "The Home Assistant tool timed out; its final "
                                "state may be unknown."
                            ),
                        },
                    )
                )
                return ToolResult(
                    text_result_for_llm=(
                        "The Home Assistant tool timed out; do not retry it "
                        "automatically because its final state may be unknown."
                    ),
                    result_type="timeout",
                )

            if len(results) != 1:
                return ToolResult(
                    text_result_for_llm=(
                        "Home Assistant returned an invalid tool result."
                    ),
                    result_type="failure",
                )
            result = results[0].tool_result
            failed = "error" in result or result.get("success") is False
            return ToolResult(
                text_result_for_llm=json_dumps(result),
                result_type="failure" if failed else "success",
            )

    async def _async_record_failure(
        self,
        invocation: ToolInvocation,
        message: str,
    ) -> ToolResult:
        """Record a rejected call and synthetic result in ChatLog."""
        chat_log = self._chat_log
        if chat_log is None:
            return ToolResult(
                text_result_for_llm="Home Assistant tools are unavailable.",
                result_type="failure",
            )
        tool_input = llm.ToolInput(
            tool_name=invocation.tool_name,
            tool_args=(
                invocation.arguments if isinstance(invocation.arguments, dict) else {}
            ),
            id=invocation.tool_call_id,
        )
        result = {"error": "ToolCallRejected", "error_text": message}
        completed = chat_log.hass.async_create_task(
            _async_return(result),
            name=f"llm_tool_{invocation.tool_call_id}_rejected",
        )
        async for _tool_result in chat_log.async_add_assistant_content(
            conversation.AssistantContent(
                agent_id=self._agent_id,
                tool_calls=[tool_input],
            ),
            tool_call_tasks={invocation.tool_call_id: completed},
        ):
            pass
        return ToolResult(
            text_result_for_llm=json_dumps(result),
            result_type="rejected",
        )

    @staticmethod
    def _security_rejection(
        chat_log: conversation.ChatLog,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str | None:
        """Reject direct security-sensitive actions before HA execution."""
        base_name = tool_name.rsplit("__", 1)[-1]
        lower_name = tool_name.lower()
        targets = _resolve_target_states(chat_log, arguments)

        if "disarm" in lower_name:
            return "Alarm disarming is disabled for this assistant."
        lock_targeted = _arguments_include_domain(arguments, "lock") or any(
            state.domain == "lock" for state in targets
        )
        if lock_targeted and (
            base_name in {intent.INTENT_TURN_OFF, intent.INTENT_TOGGLE}
            or "unlock" in lower_name
        ):
            return "Unlocking doors is disabled for this assistant."
        sensitive_cover_targeted = any(_is_sensitive_cover(state) for state in targets)
        if sensitive_cover_targeted and (
            base_name
            in {
                intent.INTENT_TURN_ON,
                intent.INTENT_SET_POSITION,
                intent.INTENT_TOGGLE,
            }
            or "open" in lower_name
        ):
            return "Opening doors, gates, and garage doors is disabled."
        alarm_targeted = _arguments_include_domain(
            arguments,
            "alarm_control_panel",
        ) or any(state.domain == "alarm_control_panel" for state in targets)
        if alarm_targeted and base_name in {
            intent.INTENT_TURN_OFF,
            intent.INTENT_TOGGLE,
        }:
            return "Alarm disarming is disabled for this assistant."
        return None


async def _async_return(result: dict[str, Any]) -> dict[str, Any]:
    """Return a precomputed tool result."""
    return result


def _resolve_target_states(
    chat_log: conversation.ChatLog,
    arguments: dict[str, Any],
) -> list[Any]:
    """Resolve common Home Assistant intent targets for policy checks."""
    direct_ids = _find_entity_ids(arguments)
    direct_states = [
        state
        for entity_id in direct_ids
        if (state := chat_log.hass.states.get(entity_id)) is not None
    ]
    if direct_states:
        return direct_states

    name = arguments.get("name")
    area = arguments.get("area")
    domains = arguments.get("domain")
    if isinstance(domains, str):
        domains = [domains]
    elif not isinstance(domains, list):
        domains = None
    if not any((name, area, domains)):
        return []

    try:
        result = intent.async_match_targets(
            chat_log.hass,
            intent.MatchTargetsConstraints(
                name=name if isinstance(name, str) else None,
                area_name=area if isinstance(area, str) else None,
                domains=domains,
                allow_duplicate_names=True,
            ),
        )
    except TypeError, ValueError:
        LOGGER.debug("Could not resolve Home Assistant tool targets for policy")
        return []
    return list(result.states) if result.is_match else []


def _find_entity_ids(value: Any) -> set[str]:
    """Find explicit entity IDs in nested tool arguments."""
    if isinstance(value, str):
        return {value} if "." in value else set()
    if isinstance(value, list):
        return set().union(*(_find_entity_ids(item) for item in value))
    if isinstance(value, dict):
        return set().union(*(_find_entity_ids(item) for item in value.values()))
    return set()


def _arguments_include_domain(arguments: dict[str, Any], domain: str) -> bool:
    """Return whether arguments explicitly select a domain."""
    domains = arguments.get("domain")
    return domains == domain or (isinstance(domains, list) and domain in domains)


def _is_sensitive_cover(state: Any) -> bool:
    """Return whether a cover opens a security boundary."""
    return state.domain == "cover" and state.attributes.get("device_class") in (
        _SENSITIVE_COVER_CLASSES
    )
