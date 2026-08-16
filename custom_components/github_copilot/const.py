"""Constants for github_copilot."""

from logging import Logger, getLogger

LOGGER: Logger = getLogger(__package__)

DOMAIN = "github_copilot"
ATTRIBUTION = "Powered by GitHub Copilot SDK"

# Configuration constants
CONF_API_TOKEN = "api_token"  # noqa: S105
CONF_AI_TASK_MODEL = "ai_task_model"
CONF_MODEL = "model"
CONF_CLI_URL = "cli_url"
CONF_CONNECTION_TOKEN = "connection_token"  # noqa: S105
CONF_MAX_TOOL_CALLS = "max_tool_calls"
CONF_SESSION_TIMEOUT = "session_timeout"
CONF_TIMEOUT = "timeout"

# Default values
DEFAULT_MODEL = "auto"
DEFAULT_CLI_URL = ""
DEFAULT_CONNECTION_TOKEN = ""
LEGACY_DEFAULT_PROMPT = """You are the household's Home Assistant voice agent.
Answer truthfully in concise, conversational plain text suitable for speech.
Do not use Markdown or provide URLs unless the user asks for them.
You cannot control home devices yet. Say so clearly instead of claiming an
action was completed.
"""
DEFAULT_PROMPT = """You are the household's Home Assistant voice agent.
Answer truthfully in concise, conversational plain text suitable for speech.
Do not use Markdown or provide URLs unless the user asks for them.
Use only the Home Assistant tools provided in the current conversation.
Never claim an action completed unless its tool result confirms success.
Say clearly when a requested entity or action is unavailable.
"""
DEFAULT_MAX_TOOL_CALLS = 10
DEFAULT_SESSION_TIMEOUT = 3600
DEFAULT_TIMEOUT = 120

# Map of legacy model IDs (stored in old config entries) to current model IDs.
LEGACY_MODEL_MAP: dict[str, str] = {
    "gpt-4o": "auto",
    "gpt-4o-mini": "gpt-5-mini",
    "claude-3.5-sonnet": "claude-sonnet-4.6",
    "claude-3-5-sonnet": "claude-sonnet-4.6",
    "claude-3.7-sonnet": "claude-sonnet-4.6",
    "claude-sonnet-4.5": "claude-sonnet-4.6",
}
