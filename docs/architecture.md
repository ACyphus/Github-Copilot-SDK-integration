# Architecture

## Components

```text
Home Assistant
  |
  +-- GitHub Copilot custom integration
        |
        +-- Conversation entity
        +-- Official Python Copilot SDK
        +-- Empty-mode sessions
        +-- Default-deny permission handler
        |
        +-- Local bundled runtime, or
        +-- Authenticated JSON-RPC connection
              |
              v
        GitHub Copilot Bridge add-on
          +-- Copilot CLI runtime
          +-- GitHub credential
          +-- Loopback runtime listener
          +-- Supervisor-network proxy
```

## Runtime boundary

The official Python SDK remains in Home Assistant Core. Its wheel is universal
and does not require the native runtime to load inside Core when a remote URI
connection is used.

The bridge add-on runs Copilot CLI. The CLI listens on loopback. A `socat`
proxy exposes the authenticated JSON-RPC port to the Supervisor network because
current Copilot CLI releases no longer provide an external bind option.

The integration and bridge share a separate connection token through
`COPILOT_CONNECTION_TOKEN`.

## Conversation handling

Home Assistant owns conversation IDs, user content, assistant content, result
construction, and TTS through `ChatLog`.

The integration keeps an active Copilot SDK session for each Home Assistant
conversation. SDK sessions receive:

- A replacement Home Assistant system prompt
- Current date and time on every user turn
- No built-in tools

`ChatLog` is the conversation authority. When an SDK session is recreated, the
integration prepends a bounded transcript of recent ChatLog user and assistant
messages to the first new turn. It does not replay prior turns as separate model
requests.

Per-conversation locks serialize overlapping turns while allowing separate
conversations to run concurrently. Inactive SDK sessions expire using monotonic
time.

Each completed turn emits content-free INFO metrics for latency analysis:

- Total Copilot request duration
- SDK-reported time to first token and model duration, when available
- Inter-token latency and token counts, when available
- Input and response character counts
- Whether the Home Assistant conversation reused an existing SDK session

The latest values are also published as attributes on the conversation entity,
which makes them visible through Home Assistant state history even when INFO logs
are suppressed.

Prompts, responses, conversation IDs, and provider request IDs are never included
in these metric logs.

## Streaming responses

Copilot SDK sessions enable streaming events. Root-agent
`assistant.message_delta` events are forwarded into Home Assistant through
`ChatLog.async_add_delta_content_stream()`, allowing Assist pipelines to begin
downstream streaming before the model finishes.

The SDK's final assistant message remains authoritative. The integration appends
any final suffix missing from the delta stream and rejects a final response that
does not begin with the already-streamed content. Cancellation aborts the active
SDK turn and removes its event listener.

## Authentication boundaries

- The GitHub token authenticates Copilot CLI to GitHub.
- The connection token authenticates Home Assistant Core to the bridge.
- Remote-mode integration entries do not retain the GitHub token.
- Local mode requires an explicit GitHub token and disables ambient CLI or
  GitHub CLI authentication.

## Tool policy

SDK clients use `mode="empty"`.

Sessions currently have:

- `available_tools=[]`
- No file hooks
- No host Git operations
- No skills
- No session store
- A permission callback that rejects every runtime tool

Future native Home Assistant tools will be registered explicitly from the
Home Assistant LLM API.

## Session lifecycle

- A Home Assistant conversation ID maps to one SDK session.
- Broken sessions are evicted.
- Sessions are disconnected and deleted on expiration or unload.
- The SDK client stops when the config entry unloads.

## Planned extensions

1. Translate native Assist LLM tools into SDK custom tools.
2. Add an AI Task entity with structured output.
3. Add Home Assistant media attachments as SDK image blobs.
