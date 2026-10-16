"""Pipecat Live custom integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant

from .const import VERSION
from .views import register_proxy_views

PLATFORMS = [Platform.CONVERSATION, Platform.STT, Platform.TTS]
if hasattr(Platform, "AI_TASK"):
    PLATFORMS.append(Platform.AI_TASK)
CARD_RESOURCES = [
    {"path": "/pipecat_live/pipecat-live-card.js", "file": "pipecat-live-card.js"},
    {"path": "/pipecat_live/pipecat-orb-card.js", "file": "pipecat-orb-card.js"},
    {"path": "/pipecat_live/pipecat-humane-card.js", "file": "pipecat-humane-card.js"},
    {"path": "/pipecat_live/pipecat-wave-card.js", "file": "pipecat-wave-card.js"},
]
CARD_MODULE_URLS = [f"{item['path']}?v={VERSION}" for item in CARD_RESOURCES]


def _is_pipecat_card_resource_url(url: str) -> bool:
    """Return true for old or current Pipecat Live Lovelace card resources."""

    path = url.split("?")[0]
    return any(path == item["path"] or path.endswith(item["file"]) for item in CARD_RESOURCES) or "pipecat_live" in path


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Pipecat Live from a config entry."""

    await _async_register_static_path(hass)
    register_proxy_views(hass)
    _async_register_frontend_module(hass)
    await _async_register_lovelace_resource(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Pipecat Live."""

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        _async_unregister_frontend_module(hass)
    return unloaded


def _async_register_frontend_module(hass: HomeAssistant) -> None:
    """Load the Lovelace card modules automatically with the HA frontend."""

    from homeassistant.components import frontend

    for url in CARD_MODULE_URLS:
        frontend.add_extra_js_url(hass, url)


def _async_unregister_frontend_module(hass: HomeAssistant) -> None:
    """Unload the Lovelace card modules when the integration is unloaded."""

    from homeassistant.components import frontend

    for url in CARD_MODULE_URLS:
        frontend.remove_extra_js_url(hass, url)


async def _async_register_lovelace_resource(hass: HomeAssistant) -> None:
    """Make the Lovelace cards visible in the dashboard card picker."""

    try:
        from homeassistant.components.lovelace.const import (
            CONF_RESOURCE_TYPE_WS,
            LOVELACE_DATA,
            MODE_STORAGE,
        )
    except ImportError:
        return

    lovelace_data = hass.data.get(LOVELACE_DATA)
    if not lovelace_data or lovelace_data.resource_mode != MODE_STORAGE:
        return

    resources = lovelace_data.resources
    if hasattr(resources, "async_get_info"):
        await resources.async_get_info()
    elif not getattr(resources, "loaded", True):
        await resources.async_load()
        resources.loaded = True

    existing_items = resources.async_items() or []
    for card_url in CARD_MODULE_URLS:
        card_base = card_url.split("?")[0]
        matched = [
            item for item in existing_items
            if str(item.get(CONF_URL, "")).split("?")[0] == card_base
        ]
        if matched:
            for item in matched:
                if str(item.get(CONF_URL, "")) != card_url and hasattr(resources, "async_update_item"):
                    await resources.async_update_item(
                        item["id"],
                        {CONF_RESOURCE_TYPE_WS: "module", CONF_URL: card_url},
                    )
        else:
            await resources.async_create_item(
                {CONF_RESOURCE_TYPE_WS: "module", CONF_URL: card_url}
            )


async def _async_register_static_path(hass: HomeAssistant) -> None:
    """Expose Lovelace card assets from the integration."""

    www_path = Path(__file__).parent / "www"
    route = "/pipecat_live"
    try:
        from homeassistant.components.http import StaticPathConfig
    except ImportError:
        StaticPathConfig = None  # type: ignore[assignment]

    if StaticPathConfig and hasattr(hass.http, "async_register_static_paths"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(route, str(www_path), True)]
        )
        return

    try:
        from homeassistant.components.http import async_register_static_paths
    except ImportError:
        async_register_static_paths = None  # type: ignore[assignment]

    if StaticPathConfig and async_register_static_paths:
        await async_register_static_paths(
            hass,
            [StaticPathConfig(route, str(www_path), True)],
        )
        return

    if hasattr(hass.http, "register_static_path"):
        hass.http.register_static_path(route, str(www_path), True)
        return

    raise RuntimeError("Home Assistant HTTP static path registration is unavailable")
