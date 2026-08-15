# ADR 0001: Keep the SDK in Core and isolate the runtime

Date: 2026-08-15

## Status

Accepted.

## Context

The original integration used a patched Copilot SDK 0.1.22 wheel because newer
preview releases shipped platform-specific wheels that did not install on Home
Assistant OS. It connected to Copilot CLI v1.0.13 in an add-on.

The generally available Copilot SDK 1.0.11 now ships a universal
`py3-none-any` wheel and supports:

- URI runtime connections
- Connection-token authentication
- Empty client mode
- Tool allowlists
- Custom tools
- Image blob attachments
- Usage events

## Decision

Keep the official Python SDK in Home Assistant Core and run the native Copilot
runtime in the bridge add-on.

Use:

- `CopilotClient(mode="empty")`
- `RuntimeConnection.for_uri(..., connection_token=...)`
- Empty tool allowlists by default
- A default-deny permission handler
- Copilot CLI release v1.0.80

The bridge binds Copilot CLI to loopback and uses `socat` to expose the
authenticated endpoint on the Supervisor network.

## Evidence

The live Phase 1 probes verified:

- SDK authentication and model discovery
- Local and remote runtime connections
- Connection-token rejection and acceptance
- Authenticated loopback-to-network proxy forwarding
- Client-side custom-tool round trips over the remote connection
- Built-in shell exclusion
- Image blob input
- Usage events
- Runtime restart producing a recoverable connection error

## Consequences

- The patched SDK wheel and build workflow are removed.
- Integration and add-on upgrades must validate an SDK/CLI pair.
- The connection token becomes required for bridge mode.
- Arbitrary MCP configuration is removed.
- Native Home Assistant tools can remain client-side in Core.
