# GitHub Copilot Home Assistant Integration

A Home Assistant custom integration that uses the official GitHub Copilot SDK
as a conversation agent.

The current foundation provides:

- A current Home Assistant `ChatLog` conversation entity.
- Multi-turn Copilot sessions.
- Home Assistant-templated conversation instructions.
- Bounded ChatLog context restoration when SDK sessions are recreated.
- Streaming model responses through Home Assistant Assist pipelines.
- Native Home Assistant LLM tools for exposed entity state and control.
- Tool calls executed through Home Assistant `ChatLog` and request context.
- Read-only Recorder history summaries for exposed entities.
- Bounded tool calls with direct unlock, garage opening, and alarm disarm blocked.
- Content-free latency and token metrics on the conversation entity.
- Dynamic model discovery after setup.
- A local SDK runtime option.
- An authenticated Home Assistant add-on for remote runtime isolation.
- Default-deny runtime tool permissions.

AI Task entities and camera attachments are planned follow-up work. See
[`docs/architecture.md`](docs/architecture.md).

## Requirements

- Home Assistant 2026.8.2 or later.
- An active GitHub Copilot subscription.
- A supported GitHub OAuth user token or fine-grained personal access token.

## Installation

### Custom integration

Install the integration through HACS as a custom repository, or copy
`custom_components/github_copilot` into the Home Assistant configuration
directory.

Restart Home Assistant, then add **GitHub Copilot** from
**Settings > Devices & services**.

Use the integration options to configure:

- Copilot model
- Conversation instructions
- Home Assistant LLM APIs
- Maximum tool calls per turn
- Response timeout
- Conversation inactivity timeout

### Runtime options

#### Bundled local runtime

Leave the bridge URL empty and provide a supported GitHub token. The official
Python SDK downloads and manages its compatible runtime.

This mode is convenient for development. The bridge add-on is preferred on Home
Assistant OS because it isolates the native runtime from Home Assistant Core.

#### GitHub Copilot Bridge add-on

1. Add this repository to the Home Assistant add-on store.
2. Install **GitHub Copilot Bridge**.
3. Configure two different secrets:

   ```yaml
   github_token: "github_pat_..."
   connection_token: "a-long-random-bridge-secret"
   ```

4. Start the add-on.
5. Configure the integration with:
   - Bridge URL: `<add-on-hostname>:8000`
   - Bridge Connection Token: the same `connection_token`

The GitHub token authenticates the add-on to GitHub. The connection token
authenticates Home Assistant Core to the add-on's JSON-RPC endpoint.

The endpoint is available only on the Supervisor network by default.

For unpublished local development on Home Assistant OS 2026.4 or later, copy
the add-on directory to:

```text
/mnt/data/supervisor/apps/local/github_copilot_bridge
```

Older instructions that use `/mnt/data/supervisor/addons/local` target the
legacy directory and will not be discovered by current Supervisor releases.

## Security model

The SDK client runs in `empty` mode. Conversation sessions start with:

- No built-in shell or filesystem tools.
- No built-in MCP servers.
- No skills or session store.
- A default-deny permission handler.

Home Assistant tools are registered explicitly from the selected native LLM
APIs. The SDK receives an exact custom-tool allowlist, while Home Assistant
retains entity exposure, argument validation, user context, and execution.
Direct unlocking, garage/gate opening, and alarm disarming are blocked.
Arbitrary MCP configuration is intentionally not supported by the bridge.

The integration also adds a read-only `GetHistory` tool. It can summarize up to
seven days for five exposed entities per call, returning numeric min/max,
time-weighted average and trend data or state durations and change counts. Raw
Recorder timelines are not sent to Copilot.

Copilot receives a replacement Home Assistant system prompt rather than its
default coding-agent prompt. Current date and time are supplied per turn so
long-running conversations do not retain stale time context.

See [`docs/threat-model.md`](docs/threat-model.md).

## Development

Create an environment with Python 3.14:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run local validation:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format . --check
.venv/bin/python scripts/check_dependencies.py
bash -n addon/run.sh
```

Run the opt-in live SDK boundary test with locally stored Copilot credentials:

```bash
.venv/bin/python scripts/sdk_boundary_smoke.py
```

Or against a running authenticated bridge:

```bash
COPILOT_RUNTIME_URL=bridge-hostname:8000 \
COPILOT_CONNECTION_TOKEN='your-bridge-secret' \
.venv/bin/python scripts/sdk_boundary_smoke.py
```

The live test verifies authentication, model listing, a client-side custom-tool
round trip, built-in tool exclusion, image input, and usage events.

An opt-in Home Assistant conversation smoke test is also available:

```bash
COPILOT_RUNTIME_URL=bridge-hostname:8000 \
COPILOT_CONNECTION_TOKEN='your-bridge-secret' \
.venv/bin/python -m pytest -q tests/test_live_conversation.py
```

## Documentation

- [Architecture](docs/architecture.md)
- [Phase 1 foundation](docs/phase-1-foundation.md)
- [Threat model](docs/threat-model.md)
- [SDK boundary decision](docs/decisions/0001-sdk-boundary.md)
- [Conversation memory decision](docs/decisions/0002-conversation-memory.md)

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).

This project is not affiliated with GitHub or Microsoft.
