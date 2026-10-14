"""Regression coverage for Home Assistant's schema converter transition."""

from __future__ import annotations

import json

from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import llm, selector

from custom_components.github_copilot.schema import convert, vol


def test_converter_matches_home_assistant() -> None:
    """Use the native converter, even when the old library remains installed."""
    if hasattr(llm, "probatio"):
        expected = llm.probatio.to_openapi
    else:
        expected = llm.to_openapi if hasattr(llm, "to_openapi") else llm.convert
    assert convert is expected


def test_schema_with_selectors_and_plain_validators() -> None:
    """The serializer's unsupported sentinel must never leak into JSON."""
    schema = vol.Schema(
        {
            vol.Required("name"): cv.string,
            vol.Required("enabled"): selector.BooleanSelector(),
            vol.Optional("entity_id"): cv.entity_id,
            vol.Optional("hours", default=24): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=168)
            ),
        }
    )

    result = convert(schema, custom_serializer=llm.selector_serializer)

    assert json.loads(json.dumps(result)) == result
    assert result["type"] == "object"
    assert result["properties"]["name"]["type"] == "string"
    assert result["properties"]["enabled"]["type"] == "boolean"
    assert result["properties"]["entity_id"]["type"] == "string"
    assert result["properties"]["hours"]["minimum"] == 1
    assert result["properties"]["hours"]["maximum"] == 168
    assert set(result["required"]) == {"name", "enabled"}
