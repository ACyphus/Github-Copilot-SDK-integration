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

1. Replace the legacy conversation handler with Home Assistant `ChatLog`.
2. Translate native Assist LLM tools into SDK custom tools.
3. Add an AI Task entity with structured output.
4. Add Home Assistant media attachments as SDK image blobs.
