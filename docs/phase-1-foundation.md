# Phase 1 Foundation

Date: 2026-08-15

## Baseline

- Commit: `168147a1ed15ef82b152196b76246c1b791618dd`
- Branch created: `phase-1-foundation`
- Host: macOS arm64
- Host Python: 3.14.5
- Push URLs remain disabled for `origin` and `upstream`

The unchanged dependency set failed to install on Python 3.14 because Home
Assistant 2024.12.3 included dependencies restricted to older Python releases.
The host also did not have the repository's pinned Ruff version.

The original runtime set was:

| Component | Original |
|---|---:|
| Home Assistant | 2024.12.3 |
| Dev container Python | 3.13 |
| Copilot SDK | patched 0.1.22+ha |
| Copilot CLI | 1.0.13 |
| Ruff | 0.15.18 |
| Test suite | none |

## Selected compatible set

| Component | Selected |
|---|---:|
| Home Assistant | 2026.8.2 |
| Python | 3.14+ |
| Copilot SDK | 1.0.11 |
| Copilot CLI release | 1.0.80 |
| Ruff | 0.16.3 |
| pip | 26.2.1 |
| colorlog | 6.12.0 |
| pytest | 9.0.3 |
| pytest-homeassistant-custom-component | 0.13.356 |

pytest 9.0.3 is the latest version compatible with the current Home Assistant
test plugin, which pins that version.

Immutable PyPI artifact URLs are used for newly published packages that were
visible through PyPI metadata but not yet visible through the configured simple
package index.

## Boundary results

Verified with live Copilot credentials:

- Official SDK authentication
- 23 available models
- Vision capability metadata
- Remote URI connection
- Valid connection token accepted
- Invalid connection token rejected
- Loopback runtime successfully exposed through an authenticated `socat` proxy
- Custom tool executed in Home Assistant's SDK client process
- Shell tool unavailable
- Synthetic PNG attachment classified correctly
- Usage events emitted

The SDK's `empty` mode and per-session tool allowlist provide the required
default-deny foundation.

## Local commands

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format . --check
.venv/bin/python scripts/check_dependencies.py
bash -n addon/run.sh
```

The live boundary probe is intentionally separate:

```bash
.venv/bin/python scripts/sdk_boundary_smoke.py
```

## Remaining scope

The current conversation platform still uses the legacy Home Assistant
conversation handler. Migration to `ChatLog`, native Home Assistant tools, AI
Task, and production image attachments belongs to later phases.
