"""Home Assistant MCP Server add-on auto-detection and MCP check helpers."""

from __future__ import annotations

import os
import re
from contextlib import suppress
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import IntegrationConfig, RuntimeConfig
from app.mcp_bridge import check_mcp, clear_mcp_tools_cache
from app.state import SUPERVISOR_URL

HA_MCP_ADDON_PORT = 9583
HA_MCP_ADDON_SECRET_RE = re.compile(
    r"MCP Server URL(?:\s*\([^)]+\))?:\s*(?P<url>https?://[^\s\"'<>]+)",
    re.IGNORECASE,
)


def _supervisor_headers() -> dict[str, str]:
    token = os.getenv("SUPERVISOR_TOKEN", "")
    if not token:
        raise RuntimeError("Supervisor token is not available in this add-on.")
    return {"Authorization": f"Bearer {token}"}


def _supervisor_payload(payload: Any) -> Any:
    if isinstance(payload, dict) and "data" in payload:
        return payload.get("data")
    return payload


def _looks_like_ha_mcp_addon(addon: dict[str, Any]) -> bool:
    slug = str(addon.get("slug") or "").lower()
    name = str(addon.get("name") or "").lower()
    image = str(addon.get("image") or "").lower()
    repository = str(addon.get("repository") or addon.get("url") or "").lower()
    return (
        slug == "ha_mcp"
        or slug == "ha_mcp_dev"
        or slug.endswith("_ha_mcp")
        or slug.endswith("_ha_mcp_dev")
        or "home assistant mcp server" in name
        or "homeassistant-ai/ha-mcp" in image
        or "homeassistant-ai/ha-mcp" in repository
    )


def _extract_ha_mcp_secret_path(text: str) -> tuple[str, str]:
    for match in HA_MCP_ADDON_SECRET_RE.finditer(text or ""):
        raw_url = match.group("url").rstrip(".,;)")
        path = urlsplit(raw_url).path.strip("/")
        if path and path != "settings":
            return path, "logs"
    return "", ""


def _ha_mcp_secret_path_from_options(addon: dict[str, Any]) -> tuple[str, str]:
    options = addon.get("options")
    if not isinstance(options, dict):
        return "", ""
    value = str(options.get("secret_path") or "").strip().strip("/")
    return (value, "options") if value else ("", "")


def _ha_mcp_addon_port(addon: dict[str, Any]) -> int:
    ports = addon.get("ports")
    if isinstance(ports, dict):
        for key, value in ports.items():
            if str(key).startswith(f"{HA_MCP_ADDON_PORT}/"):
                with suppress(TypeError, ValueError):
                    port = int(value)
                    if port > 0:
                        return port
    return HA_MCP_ADDON_PORT


def _ha_mcp_local_url(addon: dict[str, Any], path: str) -> str:
    port = _ha_mcp_addon_port(addon)
    return f"http://127.0.0.1:{port}/{path.strip('/')}"


async def _supervisor_json(client: httpx.AsyncClient, path: str) -> Any:
    response = await client.get(f"{SUPERVISOR_URL}{path}", headers=_supervisor_headers())
    response.raise_for_status()
    return _supervisor_payload(response.json())


async def _supervisor_text(client: httpx.AsyncClient, path: str) -> str:
    response = await client.get(f"{SUPERVISOR_URL}{path}", headers=_supervisor_headers())
    response.raise_for_status()
    return response.text


async def detect_ha_mcp_server_addon() -> dict[str, Any]:
    """Detect the homeassistant-ai/ha-mcp add-on endpoint through Supervisor."""

    try:
        headers = _supervisor_headers()
    except RuntimeError as err:
        return {"ok": False, "error": str(err)}

    try:
        async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
            addons_payload = await _supervisor_json(client, "/addons")
            addons = addons_payload.get("addons") if isinstance(addons_payload, dict) else []
            if not isinstance(addons, list):
                addons = []
            addon = next(
                (item for item in addons if isinstance(item, dict) and _looks_like_ha_mcp_addon(item)),
                None,
            )
            if not addon:
                return {
                    "ok": False,
                    "installed": False,
                    "error": "Home Assistant MCP Server add-on was not found.",
                }

            slug = str(addon.get("slug") or "").strip()
            if slug:
                with suppress(Exception):
                    info = await _supervisor_json(client, f"/addons/{slug}/info")
                    if isinstance(info, dict):
                        addon = {**addon, **info, "slug": slug}

            path, source = _ha_mcp_secret_path_from_options(addon)
            if not path and slug:
                with suppress(Exception):
                    logs = await _supervisor_text(client, f"/addons/{slug}/logs")
                    path, source = _extract_ha_mcp_secret_path(logs)

            if not path:
                return {
                    "ok": False,
                    "installed": True,
                    "running": str(addon.get("state") or "").lower() == "started",
                    "slug": slug,
                    "error": (
                        "Could not detect the HA MCP secret URL. Start or restart the "
                        "Home Assistant MCP Server add-on, then try auto-detect again."
                    ),
                }

            return {
                "ok": True,
                "installed": True,
                "running": str(addon.get("state") or "").lower() == "started",
                "slug": slug,
                "url": _ha_mcp_local_url(addon, path),
                "source": source,
            }
    except httpx.HTTPStatusError as err:
        return {
            "ok": False,
            "error": f"Supervisor API returned HTTP {err.response.status_code}.",
        }
    except httpx.HTTPError as err:
        return {"ok": False, "error": f"Supervisor API is not reachable: {err}"}


def _is_http_endpoint(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _mcp_endpoint_for_integration(integration: IntegrationConfig) -> tuple[str, str]:
    url = (integration.base_url or integration.endpoint).strip()
    token = ""
    if integration.kind != "ha_mcp":
        token = (integration.token or integration.api_key).strip()
    return url, token


async def _check_integration_mcp(
    config: RuntimeConfig,
    integration: IntegrationConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    flow = config.selected_flow(payload.get("flow_id"))
    refresh = bool(payload.get("refresh", True))
    if refresh:
        clear_mcp_tools_cache()

    if integration.kind == "home_assistant_mcp":
        url = config.effective_mcp_url
        token = config.effective_mcp_token
    else:
        url, token = _mcp_endpoint_for_integration(integration)

    if not _is_http_endpoint(url):
        return {
            "ok": False,
            "error": "MCP server URL is missing.",
            "tool_count": 0,
            "active_tool_count": 0,
            "tools": [],
            "active_tools": [],
            "tool_details": [],
        }

    return await check_mcp(
        url,
        token,
        flow.mcp_tool_allowlist,
        server_id=integration.id,
        server_name=integration.name,
        cache_enabled=config.mcp_tools_cache_enabled,
        cache_ttl_seconds=config.mcp_tools_cache_ttl_seconds,
        refresh=refresh,
    )
