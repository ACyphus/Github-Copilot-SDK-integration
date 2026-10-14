#!/usr/bin/env python3
"""Verify that direct dependencies use the latest compatible stable versions."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.request import urlopen

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"
DOCKERFILE = ROOT / "addon" / "Dockerfile"
PYPI_API = "https://pypi.org/pypi/{package}/json"
CLI_RELEASE_API = "https://api.github.com/repos/github/copilot-cli/releases/latest"
CONVERSATION_MANIFEST = (
    "https://raw.githubusercontent.com/home-assistant/core/{version}/"
    "homeassistant/components/conversation/manifest.json"
)
WORKFLOWS = ROOT / ".github" / "workflows"
DIRECT_PACKAGES = (
    "colorlog",
    "gazetteer-matcher",
    "github-copilot-sdk",
    "hassil",
    "homeassistant",
    "home-assistant-intents",
    "pip",
    "PyTurboJPEG",
    "pytest",
    "pytest-homeassistant-custom-component",
    "ruff",
)
GITHUB_ACTIONS = (
    "actions/checkout",
    "actions/setup-python",
    "github/codeql-action",
    "hacs/action",
)


def _fetch_json(url: str) -> dict:
    """Fetch JSON metadata."""
    with urlopen(url, timeout=30) as response:  # noqa: S310
        return json.load(response)


def _read_pins() -> dict[str, str]:
    """Read exact dependency pins from requirements.txt."""
    pins: dict[str, str] = {}
    for line in REQUIREMENTS.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        wheel = re.search(r"/([^/]+)-([0-9][^-]+)-[^/ ]+\.whl", line)
        if wheel:
            pins[wheel.group(1).replace("_", "-")] = wheel.group(2)
            continue
        if "==" in line:
            package, version = line.split("==", maxsplit=1)
            pins[package] = version
    return pins


def _pinned_dependency_version(requirements: list[str], package: str) -> str:
    """Return an exact dependency version required by Home Assistant."""
    for requirement_text in requirements:
        requirement = Requirement(requirement_text)
        if requirement.name == package:
            match = re.fullmatch(r"==([^,; ]+)", str(requirement.specifier))
            if match:
                return match.group(1)
    msg = f"Unable to determine the required version of {package}."
    raise RuntimeError(msg)


def main() -> int:
    """Check PyPI and GitHub release metadata."""
    pins = _read_pins()
    failures: list[str] = []
    metadata = {
        package: _fetch_json(PYPI_API.format(package=package))
        for package in DIRECT_PACKAGES
    }

    conversation_requirements = _fetch_json(
        CONVERSATION_MANIFEST.format(
            version=metadata["homeassistant"]["info"]["version"]
        )
    )["requirements"]
    compatible_versions = {
        package: _pinned_dependency_version(conversation_requirements, package)
        for package in ("gazetteer-matcher", "hassil", "home-assistant-intents")
    }
    compatible_versions["pytest"] = _pinned_dependency_version(
        metadata["pytest-homeassistant-custom-component"]["info"]["requires_dist"],
        "pytest",
    )
    for package in DIRECT_PACKAGES:
        expected = compatible_versions.get(
            package, metadata[package]["info"]["version"]
        )
        actual = pins.get(package)
        status = "ok" if actual == expected else "outdated"
        print(f"{package}: {actual} ({status}, expected {expected})")
        if actual != expected:
            failures.append(f"{package}: expected {expected}, found {actual}")

    dockerfile = DOCKERFILE.read_text()
    match = re.search(r"ARG COPILOT_VERSION=v([0-9.]+)", dockerfile)
    if match is None:
        failures.append("Copilot CLI version pin was not found in addon/Dockerfile")
    else:
        actual_cli = match.group(1)
        expected_cli = _fetch_json(CLI_RELEASE_API)["tag_name"].removeprefix("v")
        status = "ok" if actual_cli == expected_cli else "outdated"
        print(f"github-copilot-cli: {actual_cli} ({status}, expected {expected_cli})")
        if actual_cli != expected_cli:
            failures.append(
                f"github-copilot-cli: expected {expected_cli}, found {actual_cli}"
            )

    workflow_text = "\n".join(
        workflow.read_text() for workflow in WORKFLOWS.glob("*.yml")
    )
    for repository in GITHUB_ACTIONS:
        latest_tag = _fetch_json(
            f"https://api.github.com/repos/{repository}/tags?per_page=1"
        )[0]
        expected_sha = latest_tag["commit"]["sha"]
        action_pattern = rf"{re.escape(repository)}(?:/[^@\s]+)?@{expected_sha}"
        status = "ok" if re.search(action_pattern, workflow_text) else "outdated"
        print(f"{repository}: {latest_tag['name']}@{expected_sha[:12]} ({status})")
        if status != "ok":
            failures.append(
                f"{repository}: expected {latest_tag['name']} at {expected_sha}"
            )

    hassfest_sha = _fetch_json(
        "https://api.github.com/repos/home-assistant/actions/commits/master"
    )["sha"]
    hassfest_status = (
        "ok"
        if f"home-assistant/actions/hassfest@{hassfest_sha}" in workflow_text
        else "outdated"
    )
    print(f"home-assistant/actions: master@{hassfest_sha[:12]} ({hassfest_status})")
    if hassfest_status != "ok":
        failures.append(f"home-assistant/actions: expected master at {hassfest_sha}")

    if not failures:
        return 0

    print("\nDependency audit failed:", file=sys.stderr)
    for failure in failures:
        print(f"- {failure}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
