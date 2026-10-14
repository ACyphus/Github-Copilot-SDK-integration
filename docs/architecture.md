# Architecture

## Components

```text
Home Assistant
  |
  +-- GitHub Copilot custom integration
        |
        +-- Conversation entity
        +-- AI Task entity
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
Local runtime clients use an explicit temporary data directory, isolated from
ambient Copilot configuration and removed when the client closes.

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

## AI Task handling

The AI Task entity supports `GENERATE_DATA` only. Every task creates a fresh SDK
session and deletes it after completion.

Plain-text tasks:

- Use the independently selected AI Task model.
- Receive no tools.
- Add the final assistant text to the ephemeral Home Assistant ChatLog.

Structured tasks:

- Convert the native Home Assistant structure to JSON Schema using its matching
  schema library (Voluptuous through 2026.9, Probatio from 2026.10).
- Register only a terminal `submit_result` custom tool.
- Validate submitted arguments with the original Home Assistant schema.
- Return the validated native data rather than parsing assistant prose.
- Allow one validation repair before failing explicitly.

AI Tasks do not receive native Assist tools or `GetHistory`. Automations gather
deterministic context before invoking the task.

## AI Task image attachments

Home Assistant resolves camera and local-media attachments into temporary local
paths. The integration never passes those paths to the SDK.

For every explicit image attachment it:

1. Verifies the selected model's live vision capability and limits.
2. Intersects model limits with configured count, byte, and dimension limits.
3. Accepts only JPEG, PNG, and WebP still images.
4. Reads with a strict encoded-byte bound.
5. Decodes in the executor with decoded-pixel protections.
6. Applies EXIF orientation, resizes, and strips metadata through re-encoding.
7. Creates an in-memory base64 SDK blob attachment.
8. Releases task data when the one-shot session ends.

Structured image tasks still expose only `submit_result`. Image support does
not add Home Assistant control, history, shell, filesystem, MCP, skills, or
session-store access.

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

- An exact `available_tools` allowlist containing only translated Home Assistant
  tools
- No file hooks
- No host Git operations
- No skills
- No session store
- A permission callback that rejects every runtime tool not handled by the
  integration

Home Assistant `llm.Tool` schemas are converted to Copilot SDK custom tools.
Their handlers call the current `ChatLog` LLM API instance, so Home Assistant
retains exposure checks, request context, validation, traces, and execution.
The session dispatcher is rebound on every serialized conversation turn.
HA 2026.10 typed tool results preserve their native error flag. Earlier HA
versions continue to use their original dictionary-based tool results.

The integration appends one custom read-only Home Assistant tool:

- `GetHistory` resolves only entities exposed to the active assistant.
- Recorder queries run in the recorder executor.
- Requests are limited to five entities and seven days.
- Numeric history returns bounded aggregate statistics.
- Non-numeric history returns state durations and transition counts.
- Raw state timelines are never returned to Copilot.

Future native Home Assistant tools will be registered explicitly from the
Home Assistant LLM API.

## Session lifecycle

- A Home Assistant conversation ID maps to one SDK session.
- Broken sessions are evicted.
- Sessions are disconnected and deleted on expiration or unload.
- The SDK client stops when the config entry unloads.

## Planned extensions

1. Add further event-gated household vision automations.
