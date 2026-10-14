# GitHub Copilot Instructions

## Project

This repository contains a Home Assistant custom integration backed by the
official GitHub Copilot SDK and an optional Home Assistant bridge add-on.

Current platforms:

- `conversation`
- `ai_task`

Planned platforms:

- Additional AI Task and vision automations

## Supported foundation

- Home Assistant 2026.8.2+ (validation baseline: 2026.10.1)
- Python 3.14+
- GitHub Copilot SDK 1.0.21
- GitHub Copilot CLI release v1.0.97
- Ruff 0.17.0
- pytest with pytest-homeassistant-custom-component

Dependencies are pinned in `requirements.txt`. Some current releases use
immutable PyPI artifact URLs because the configured package mirror can lag PyPI
release metadata.

## Architecture

- The Python SDK runs in Home Assistant Core.
- Remote mode connects using `RuntimeConnection.for_uri`.
- The add-on holds the GitHub credential and runs Copilot CLI headlessly.
- A separate connection token authenticates the JSON-RPC connection.
- The add-on runtime binds to loopback; `socat` exposes the authenticated port
  to the Supervisor network.
- SDK clients and sessions use `mode="empty"`.
- Sessions start with no built-in tools and a default-deny permission handler.
- Arbitrary MCP configuration is intentionally unsupported.

Read:

- `docs/architecture.md`
- `docs/threat-model.md`
- `docs/decisions/0001-sdk-boundary.md`

## Code conventions

- Use `from __future__ import annotations`.
- Type all production Python code.
- Use async I/O.
- Follow Home Assistant patterns.
- Keep logs and errors free of credentials, prompts, and response content.
- Never weaken the default-deny tool policy.
- AI Task structured output must be validated by Home Assistant schemas.
- Image attachments must be normalized, metadata-free, size-bounded SDK blobs.
- Do not add shell, filesystem, MCP, skills, or session-store access without an
  explicit architecture and security decision.
- Update `addon/CHANGELOG.md` when changing the add-on.

## Validation

Run:

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest -q
python3 -m ruff check .
python3 -m ruff format . --check
python3 scripts/check_dependencies.py
bash -n addon/run.sh
```

The live boundary smoke test is opt-in:

```bash
python3 scripts/sdk_boundary_smoke.py
```

It must not run in ordinary CI because it consumes Copilot service usage and
requires credentials.

## Testing

- Mock the SDK boundary in ordinary tests.
- Never require credentials in the default suite.
- Use synthetic images only.
- Cover local versus remote authentication, token separation, lifecycle,
  timeout/error mapping, and tool isolation.
