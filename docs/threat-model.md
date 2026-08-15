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
| Model invokes shell or filesystem tools | Empty SDK mode, empty allowlist, default-deny callback |
| Built-in GitHub MCP exposes data | CLI starts with `--disable-builtin-mcps` |
| Arbitrary MCP server gains execution | Add-on MCP configuration removed |
| GitHub token leaks into Core | Remote entries omit the GitHub token |
| Secrets appear in logs | Errors report categories and safe hostnames only |
| Unauthenticated bridge exposure | Runtime binds loopback; authenticated proxy is Supervisor-only |
| Stale vulnerable runtime remains pinned | Automated dependency audit |
| Camera content is uploaded unexpectedly | Attachments require explicit future AI Task actions |
| Recreated model session loses or invents context | Restore only bounded user/assistant ChatLog content, labeled as prior context |
| Prior transcript is treated as system instruction | Keep transcript in the user turn and explicitly label it as non-instruction context |

## Required future controls

Before native Home Assistant tools ship:

- Tools must come from the Home Assistant LLM API.
- Entity exposure and Home Assistant context must be preserved.
- Tool schemas and results must be validated.
- Locks, doors, garage controls, and security actions need explicit safeguards.

Before image support ships:

- Enforce image count, MIME type, and byte limits.
- Do not log or persist image data.
- Document every automation that sends an image to Copilot.
