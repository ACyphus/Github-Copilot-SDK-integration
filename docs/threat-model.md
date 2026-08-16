# Threat Model

## Assets

- GitHub token with Copilot access
- Bridge connection token
- Copilot usage quota
- Home Assistant entity state
- Future camera snapshots and tool arguments

## Trust boundaries

### Home Assistant Core

Trusted to:

- Store the bridge connection token
- Select exposed Home Assistant tools
- Validate tool arguments and model output

### Bridge add-on

Trusted to:

- Store the GitHub token
- Run Copilot CLI
- Accept only authenticated JSON-RPC clients

### GitHub Copilot service

Cloud boundary receiving prompts and explicitly attached data.

## Primary threats and controls

| Threat | Control |
|---|---|
| Another add-on consumes Copilot quota | Required JSON-RPC connection token |
| Model invokes shell or filesystem tools | Empty SDK mode, exact HA custom-tool allowlist, default-deny callback |
| Built-in GitHub MCP exposes data | CLI starts with `--disable-builtin-mcps` |
| Arbitrary MCP server gains execution | Add-on MCP configuration removed |
| GitHub token leaks into Core | Remote entries omit the GitHub token |
| Secrets appear in logs | Errors report categories and safe hostnames only |
| Unauthenticated bridge exposure | Runtime binds loopback; authenticated proxy is Supervisor-only |
| Stale vulnerable runtime remains pinned | Automated dependency audit |
| Camera content is uploaded unexpectedly | Attachments require explicit future AI Task actions |
| Recreated model session loses or invents context | Restore only bounded user/assistant ChatLog content, labeled as prior context |
| Prior transcript is treated as system instruction | Keep transcript in the user turn and explicitly label it as non-instruction context |
| Model controls an unexposed entity | Tools execute through the native Home Assistant LLM API and request context |
| Model unlocks a door or opens a garage/gate | Integration rejects direct sensitive actions before Home Assistant execution |
| Tool loop consumes unbounded time or actions | Per-turn call cap, per-tool timeout, overall request timeout, no automatic state-changing retry |
| History request leaks unexposed state | `GetHistory` resolves through the active assistant's exposure policy |
| Recorder history creates excessive prompt or query size | Seven-day, five-entity bounds and aggregate-only results |
| AI Task gains device control | Task sessions use an empty or submit-only exact tool allowlist |
| Structured prose bypasses validation | Structured results are accepted only through `submit_result` and the original HA schema |
| Invalid structured output loops indefinitely | One repair attempt; the second invalid submission forces terminal failure |
| Task history leaks between automations | Every AI Task uses a new short-lived SDK session |
| Task attachments leave the network before controls exist | Phase 4 does not advertise attachment support |

## Required future controls

Before image support ships:

- Enforce image count, MIME type, and byte limits.
- Do not log or persist image data.
- Document every automation that sends an image to Copilot.
