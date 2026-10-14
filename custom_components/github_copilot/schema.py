"""Use Home Assistant's converter with its matching schema and serializer types."""

from __future__ import annotations

from homeassistant.helpers import llm

if hasattr(llm, "probatio"):
    import probatio as vol
else:
    import voluptuous as vol

try:
    from homeassistant.helpers.llm import convert
except ImportError:
    from probatio import to_openapi as convert

__all__ = ["convert", "vol"]
