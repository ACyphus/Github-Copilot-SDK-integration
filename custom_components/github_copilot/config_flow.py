"""Adds config flow for GitHub Copilot."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_LLM_HASS_API, CONF_PROMPT
from homeassistant.helpers import llm, selector

from .api import (
    GitHubCopilotApiClient,
    GitHubCopilotApiClientAuthenticationError,
    GitHubCopilotApiClientCommunicationError,
    GitHubCopilotApiClientError,
)
from .const import (
    CONF_AI_TASK_MODEL,
    CONF_API_TOKEN,
    CONF_ATTACHMENTS_ENABLED,
    CONF_CLI_URL,
    CONF_CONNECTION_TOKEN,
    CONF_MAX_ATTACHMENT_BYTES,
    CONF_MAX_ATTACHMENTS,
    CONF_MAX_IMAGE_DIMENSION,
    CONF_MAX_TOOL_CALLS,
    CONF_MODEL,
    CONF_SESSION_TIMEOUT,
    CONF_TIMEOUT,
    DEFAULT_ATTACHMENTS_ENABLED,
    DEFAULT_CLI_URL,
    DEFAULT_CONNECTION_TOKEN,
    DEFAULT_MAX_ATTACHMENT_BYTES,
    DEFAULT_MAX_ATTACHMENTS,
    DEFAULT_MAX_IMAGE_DIMENSION,
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    DEFAULT_SESSION_TIMEOUT,
    DEFAULT_TIMEOUT,
    DOMAIN,
    LEGACY_MODEL_MAP,
    LOGGER,
)


def _validate_cli_url(cli_url: str) -> bool:
    """Return whether a bridge address contains a hostname and port."""
    normalized = cli_url if "://" in cli_url else f"tcp://{cli_url}"
    try:
        parsed = urlparse(normalized)
        return bool(parsed.hostname and parsed.port)
    except ValueError:
        return False


class GitHubCopilotFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for GitHub Copilot."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> GitHubCopilotOptionsFlow:
        """Get the options flow for this handler."""
        return GitHubCopilotOptionsFlow()

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle a flow initialized by the user."""
        _errors = {}
        if user_input is not None:
            model = user_input.get(CONF_MODEL, DEFAULT_MODEL).strip()
            ai_task_model = user_input.get(CONF_AI_TASK_MODEL, model).strip()
            cli_url = user_input.get(CONF_CLI_URL, DEFAULT_CLI_URL).strip()
            api_token = user_input.get(CONF_API_TOKEN, "").strip()
            connection_token = user_input.get(
                CONF_CONNECTION_TOKEN,
                DEFAULT_CONNECTION_TOKEN,
            ).strip()
            llm_apis = user_input.get(
                CONF_LLM_HASS_API,
                [llm.LLM_API_ASSIST],
            )
            prompt = user_input.get(CONF_PROMPT, DEFAULT_PROMPT)
            session_timeout = float(
                user_input.get(CONF_SESSION_TIMEOUT, DEFAULT_SESSION_TIMEOUT)
            )

            if cli_url and not _validate_cli_url(cli_url):
                _errors[CONF_CLI_URL] = "invalid_url"
            elif cli_url and not connection_token:
                _errors[CONF_CONNECTION_TOKEN] = "connection_token_required"
            elif not cli_url and not api_token:
                _errors[CONF_API_TOKEN] = "token_required"

            if not _errors:
                try:
                    LOGGER.debug(
                        "Testing GitHub Copilot credentials with model '%s'",
                        model,
                    )
                    await self._test_credentials(
                        api_token=api_token,
                        model=model,
                        cli_url=cli_url,
                        connection_token=connection_token,
                    )
                except GitHubCopilotApiClientAuthenticationError as exception:
                    LOGGER.warning(
                        "GitHub Copilot authentication failed: %s",
                        exception,
                    )
                    _errors["base"] = "auth"
                except GitHubCopilotApiClientCommunicationError as exception:
                    LOGGER.error(
                        "Failed to connect to GitHub Copilot CLI: %s",
                        exception,
                    )
                    _errors["base"] = "connection"
                except GitHubCopilotApiClientError as exception:
                    LOGGER.exception(
                        "Unexpected error during GitHub Copilot setup: %s - %s",
                        type(exception).__name__,
                        str(exception),
                    )
                    _errors["base"] = "unknown"
                except Exception as exception:  # noqa: BLE001
                    LOGGER.exception(
                        "Unexpected error in config flow: %s - %s",
                        type(exception).__name__,
                        str(exception),
                    )
                    _errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id("github_copilot")
                    self._abort_if_unique_id_configured()
                    LOGGER.info(
                        "GitHub Copilot integration configured with model '%s'",
                        model,
                    )
                    data = {
                        **user_input,
                        CONF_MODEL: model,
                        CONF_AI_TASK_MODEL: ai_task_model,
                        CONF_CLI_URL: cli_url,
                        CONF_CONNECTION_TOKEN: connection_token,
                        CONF_LLM_HASS_API: llm_apis,
                        CONF_MAX_TOOL_CALLS: int(
                            user_input.get(CONF_MAX_TOOL_CALLS, DEFAULT_MAX_TOOL_CALLS)
                        ),
                        CONF_PROMPT: prompt,
                        CONF_SESSION_TIMEOUT: session_timeout,
                    }
                    if cli_url:
                        data.pop(CONF_API_TOKEN, None)
                    else:
                        data.pop(CONF_CONNECTION_TOKEN, None)
                    return self.async_create_entry(
                        title="GitHub Copilot",
                        data=data,
                    )

        try:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Optional(CONF_API_TOKEN): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.PASSWORD,
                            ),
                        ),
                        vol.Optional(
                            CONF_MODEL,
                            default=DEFAULT_MODEL,
                        ): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.TEXT,
                            )
                        ),
                        vol.Optional(
                            CONF_CLI_URL,
                            default=DEFAULT_CLI_URL,
                        ): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.URL,
                            ),
                        ),
                        vol.Optional(
                            CONF_CONNECTION_TOKEN,
                            default=DEFAULT_CONNECTION_TOKEN,
                        ): selector.TextSelector(
                            selector.TextSelectorConfig(
                                type=selector.TextSelectorType.PASSWORD,
                            ),
                        ),
                    },
                ),
                errors=_errors,
                description_placeholders={
                    "documentation_url": "https://github.com/ACyphus/Github-Copilot-SDK-integration",
                },
            )
        except Exception as exception:  # noqa: BLE001
            LOGGER.exception(
                "Failed to render config flow form: %s - %s. "
                "This may indicate a dependency or import issue.",
                type(exception).__name__,
                str(exception),
            )
            # Return error form with minimal schema to avoid further errors
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Optional(CONF_API_TOKEN): str,
                    }
                ),
                errors={"base": "unknown"},
            )

    async def _test_credentials(
        self,
        api_token: str,
        model: str,
        cli_url: str = DEFAULT_CLI_URL,
        connection_token: str = DEFAULT_CONNECTION_TOKEN,
    ) -> None:
        """Validate credentials."""
        client = GitHubCopilotApiClient(
            model=model,
            github_token=api_token if not cli_url else "",
            cli_url=cli_url,
            connection_token=connection_token,
        )
        try:
            await client.async_test_connection()
        except (
            GitHubCopilotApiClientAuthenticationError,
            GitHubCopilotApiClientCommunicationError,
            GitHubCopilotApiClientError,
        ):
            # Re-raise our custom exceptions as-is
            raise
        except Exception as exception:
            # Wrap any unexpected exception
            LOGGER.exception(
                "Unexpected exception during credential test: %s - %s. "
                "Full details in traceback.",
                type(exception).__name__,
                str(exception),
            )
            msg = (
                f"Unexpected error during credential validation: "
                f"{type(exception).__name__}: {exception}"
            )
            raise GitHubCopilotApiClientError(msg) from exception
        finally:
            # Always clean up the client, even on exception
            await client.async_close()


class GitHubCopilotOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for GitHub Copilot integration."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle options flow."""
        _errors: dict[str, str] = {}
        if user_input is not None:
            cli_url = user_input.get(CONF_CLI_URL, DEFAULT_CLI_URL).strip()
            connection_token = user_input.get(
                CONF_CONNECTION_TOKEN,
                DEFAULT_CONNECTION_TOKEN,
            ).strip()

            if cli_url and not _validate_cli_url(cli_url):
                _errors[CONF_CLI_URL] = "invalid_url"
            elif cli_url and not connection_token:
                _errors[CONF_CONNECTION_TOKEN] = "connection_token_required"

            if not _errors:
                data = {
                    **self.config_entry.data,
                    CONF_MODEL: user_input[CONF_MODEL].strip(),
                    CONF_AI_TASK_MODEL: user_input.get(
                        CONF_AI_TASK_MODEL,
                        self.config_entry.data.get(
                            CONF_AI_TASK_MODEL,
                            user_input[CONF_MODEL],
                        ),
                    ).strip(),
                    CONF_CLI_URL: cli_url,
                    CONF_CONNECTION_TOKEN: connection_token,
                    CONF_ATTACHMENTS_ENABLED: user_input.get(
                        CONF_ATTACHMENTS_ENABLED,
                        self.config_entry.data.get(
                            CONF_ATTACHMENTS_ENABLED,
                            DEFAULT_ATTACHMENTS_ENABLED,
                        ),
                    ),
                    CONF_MAX_ATTACHMENTS: int(
                        user_input.get(
                            CONF_MAX_ATTACHMENTS,
                            self.config_entry.data.get(
                                CONF_MAX_ATTACHMENTS,
                                DEFAULT_MAX_ATTACHMENTS,
                            ),
                        )
                    ),
                    CONF_MAX_ATTACHMENT_BYTES: int(
                        user_input.get(
                            CONF_MAX_ATTACHMENT_BYTES,
                            self.config_entry.data.get(
                                CONF_MAX_ATTACHMENT_BYTES,
                                DEFAULT_MAX_ATTACHMENT_BYTES,
                            ),
                        )
                    ),
                    CONF_MAX_IMAGE_DIMENSION: int(
                        user_input.get(
                            CONF_MAX_IMAGE_DIMENSION,
                            self.config_entry.data.get(
                                CONF_MAX_IMAGE_DIMENSION,
                                DEFAULT_MAX_IMAGE_DIMENSION,
                            ),
                        )
                    ),
                    CONF_LLM_HASS_API: user_input.get(CONF_LLM_HASS_API, []),
                    CONF_MAX_TOOL_CALLS: int(
                        user_input.get(CONF_MAX_TOOL_CALLS, DEFAULT_MAX_TOOL_CALLS)
                    ),
                    CONF_PROMPT: user_input.get(CONF_PROMPT, DEFAULT_PROMPT),
                    CONF_SESSION_TIMEOUT: float(
                        user_input.get(
                            CONF_SESSION_TIMEOUT,
                            DEFAULT_SESSION_TIMEOUT,
                        )
                    ),
                    CONF_TIMEOUT: float(user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)),
                }
                if cli_url:
                    data.pop(CONF_API_TOKEN, None)
                else:
                    data.pop(CONF_CONNECTION_TOKEN, None)
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data=data,
                )
                return self.async_create_entry(title="", data={})

        # Get current model from config entry, normalizing any legacy IDs.
        current_model = self.config_entry.data.get(CONF_MODEL, DEFAULT_MODEL)
        current_model = LEGACY_MODEL_MAP.get(current_model, current_model)
        current_ai_task_model = self.config_entry.data.get(
            CONF_AI_TASK_MODEL,
            current_model,
        )
        current_ai_task_model = LEGACY_MODEL_MAP.get(
            current_ai_task_model,
            current_ai_task_model,
        )

        # Get current CLI URL from config entry
        current_cli_url = self.config_entry.data.get(CONF_CLI_URL, DEFAULT_CLI_URL)

        current_connection_token = self.config_entry.data.get(
            CONF_CONNECTION_TOKEN,
            DEFAULT_CONNECTION_TOKEN,
        )
        current_llm_apis = self.config_entry.data.get(CONF_LLM_HASS_API, [])
        if isinstance(current_llm_apis, str):
            current_llm_apis = [current_llm_apis]
        available_llm_apis = [
            selector.SelectOptionDict(label=api.name, value=api.id)
            for api in llm.async_get_apis(self.hass)
        ]
        valid_llm_api_ids = {option["value"] for option in available_llm_apis}
        current_llm_apis = [
            api_id for api_id in current_llm_apis if api_id in valid_llm_api_ids
        ]
        current_max_tool_calls = int(
            self.config_entry.data.get(
                CONF_MAX_TOOL_CALLS,
                DEFAULT_MAX_TOOL_CALLS,
            )
        )
        current_attachments_enabled = bool(
            self.config_entry.data.get(
                CONF_ATTACHMENTS_ENABLED,
                DEFAULT_ATTACHMENTS_ENABLED,
            )
        )
        current_max_attachments = int(
            self.config_entry.data.get(
                CONF_MAX_ATTACHMENTS,
                DEFAULT_MAX_ATTACHMENTS,
            )
        )
        current_max_attachment_bytes = int(
            self.config_entry.data.get(
                CONF_MAX_ATTACHMENT_BYTES,
                DEFAULT_MAX_ATTACHMENT_BYTES,
            )
        )
        current_max_image_dimension = int(
            self.config_entry.data.get(
                CONF_MAX_IMAGE_DIMENSION,
                DEFAULT_MAX_IMAGE_DIMENSION,
            )
        )
        current_prompt = self.config_entry.data.get(CONF_PROMPT, DEFAULT_PROMPT)
        current_session_timeout = float(
            self.config_entry.data.get(
                CONF_SESSION_TIMEOUT,
                DEFAULT_SESSION_TIMEOUT,
            )
        )

        # Get current timeout from config entry
        current_timeout = float(
            self.config_entry.data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        )

        # Try to fetch the available models dynamically; fall back to the
        # hardcoded list if the API call fails or runtime_data is unavailable.
        available_models = [DEFAULT_MODEL]
        try:
            available_models = list(
                await self.config_entry.runtime_data.client.async_available_models()
            )
        except GitHubCopilotApiClientError, AttributeError:
            LOGGER.debug(
                "Could not fetch dynamic model list; using built-in fallback.",
                exc_info=True,
            )

        # Ensure the currently selected model is always present in the list so
        # the dropdown does not show a blank/invalid selection.
        if current_model not in available_models:
            available_models = [current_model, *available_models]
        if current_ai_task_model not in available_models:
            available_models = [current_ai_task_model, *available_models]

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MODEL,
                        default=current_model,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=available_models,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        ),
                    ),
                    vol.Required(
                        CONF_AI_TASK_MODEL,
                        default=current_ai_task_model,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=available_models,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        ),
                    ),
                    vol.Optional(
                        CONF_CLI_URL,
                        default=current_cli_url,
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.URL,
                        ),
                    ),
                    vol.Optional(
                        CONF_CONNECTION_TOKEN,
                        default=current_connection_token,
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD,
                        ),
                    ),
                    vol.Optional(
                        CONF_ATTACHMENTS_ENABLED,
                        default=current_attachments_enabled,
                    ): selector.BooleanSelector(),
                    vol.Optional(
                        CONF_MAX_ATTACHMENTS,
                        default=current_max_attachments,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=4,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        ),
                    ),
                    vol.Optional(
                        CONF_MAX_ATTACHMENT_BYTES,
                        default=current_max_attachment_bytes,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1 * 1024 * 1024,
                            max=20 * 1024 * 1024,
                            step=1 * 1024 * 1024,
                            unit_of_measurement="B",
                            mode=selector.NumberSelectorMode.BOX,
                        ),
                    ),
                    vol.Optional(
                        CONF_MAX_IMAGE_DIMENSION,
                        default=current_max_image_dimension,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=512,
                            max=4096,
                            step=256,
                            unit_of_measurement="px",
                            mode=selector.NumberSelectorMode.BOX,
                        ),
                    ),
                    vol.Optional(
                        CONF_LLM_HASS_API,
                        default=current_llm_apis,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=available_llm_apis,
                            multiple=True,
                        ),
                    ),
                    vol.Optional(
                        CONF_MAX_TOOL_CALLS,
                        default=current_max_tool_calls,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=20,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        ),
                    ),
                    vol.Optional(
                        CONF_PROMPT,
                        default=current_prompt,
                    ): selector.TemplateSelector(
                        selector.TemplateSelectorConfig(),
                    ),
                    vol.Optional(
                        CONF_SESSION_TIMEOUT,
                        default=current_session_timeout,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=300,
                            max=86400,
                            step=300,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.BOX,
                        ),
                    ),
                    vol.Optional(
                        CONF_TIMEOUT,
                        default=current_timeout,
                    ): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=10,
                            max=600,
                            step=10,
                            unit_of_measurement="s",
                            mode=selector.NumberSelectorMode.BOX,
                        ),
                    ),
                }
            ),
            errors=_errors,
        )
