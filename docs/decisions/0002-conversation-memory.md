# ADR 0002: ChatLog is the conversation authority

Date: 2026-08-15

## Status

Accepted.

## Context

Home Assistant maintains conversation history in `ChatLog`. Copilot SDK sessions
also retain model-side history. A runtime restart, prompt change, inactivity
expiration, or provider failure can remove the SDK session while the Home
Assistant conversation remains visible.

Replaying every historical turn as a new SDK request would consume extra model
calls and produce duplicate assistant responses.

## Decision

Treat Home Assistant `ChatLog` as the authoritative conversation record.

Use the SDK session as an active-context cache:

- Reuse it for normal multi-turn conversation.
- Mirror every successful assistant response into ChatLog.
- On SDK-session creation for an existing conversation, prepend a bounded
  transcript of prior ChatLog user and assistant messages to the current turn.
- Do not replay historical messages as separate SDK requests.
- Exclude system and tool content from the transcript.

The SDK system prompt is replaced entirely with Home Assistant instructions.
Dynamic date and time are excluded from the stable prompt fingerprint and
supplied per turn.

## Consequences

- Ordinary follow-ups use native SDK session memory.
- Recreated sessions receive recent context in one request.
- Very old context may be omitted by transcript limits.
- Persistent restoration across Home Assistant restarts remains out of scope.
