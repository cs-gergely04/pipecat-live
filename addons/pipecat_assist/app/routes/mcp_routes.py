"""MCP server check, reset, history, and HA MCP add-on detection routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.mcp_bridge import (
    check_mcp,
    clear_mcp_call_history,
    clear_mcp_tools_cache,
    list_mcp_call_history,
)
from app.services.mcp import (
    _check_integration_mcp,
    _is_http_endpoint,
    detect_ha_mcp_server_addon,
)
from app.state import STORE, _config_response

router = APIRouter()


@router.post("/api/assist/mcp/check")
async def api_check_mcp(payload: dict[str, Any] | None = None):
    config = STORE.load()
    payload = payload or {}
    flow = config.selected_flow(payload.get("flow_id"))
    refresh = bool(payload.get("refresh", True))
    if refresh:
        clear_mcp_tools_cache()
    return await check_mcp(
        config.effective_mcp_url,
        config.effective_mcp_token,
        flow.mcp_tool_allowlist,
        cache_enabled=config.mcp_tools_cache_enabled,
        cache_ttl_seconds=config.mcp_tools_cache_ttl_seconds,
        refresh=refresh,
    )


@router.post("/api/assist/mcp/reset")
async def api_reset_mcp(request: Request):
    config = STORE.reset_mcp_defaults()
    return _config_response(config, request)


@router.get("/api/assist/mcp/history")
async def api_mcp_history():
    return list_mcp_call_history()


@router.delete("/api/assist/mcp/history")
async def api_clear_mcp_history():
    return clear_mcp_call_history()


@router.post("/api/assist/integrations/{integration_id}/detect")
async def api_detect_integration(integration_id: str, request: Request):
    config = STORE.load()
    integration = config.integration(integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    if integration.kind != "ha_mcp":
        raise HTTPException(status_code=400, detail="Auto-detect is only available for the HA MCP Server add-on")

    detection = await detect_ha_mcp_server_addon()
    if detection.get("ok"):
        integration.enabled = True
        integration.base_url = str(detection.get("url") or "")
        integration.token = ""
        integration.api_key = ""
        STORE.save(config)

    data = _config_response(config, request)
    data["detection"] = detection
    return data


@router.post("/api/assist/integrations/{integration_id}/mcp/check")
async def api_check_integration_mcp(integration_id: str, request: Request, payload: dict[str, Any] | None = None):
    config = STORE.load()
    integration = config.integration(integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    if integration.kind not in {"home_assistant_mcp", "ha_mcp", "mcp_server"}:
        raise HTTPException(status_code=400, detail="Integration is not an MCP server")

    payload = payload or {}
    detection: dict[str, Any] | None = None
    if integration.kind == "ha_mcp" and not _is_http_endpoint((integration.base_url or integration.endpoint).strip()):
        detection = await detect_ha_mcp_server_addon()
        if detection.get("ok"):
            integration.enabled = True
            integration.base_url = str(detection.get("url") or "")
            integration.token = ""
            integration.api_key = ""
            STORE.save(config)
        else:
            return {
                "ok": False,
                "error": detection.get("error") or "Could not detect the HA MCP Server add-on.",
                "tool_count": 0,
                "tools": [],
                "detection": detection,
            }

    result = await _check_integration_mcp(config, integration, payload)
    if detection:
        result["detection"] = detection
        result["config"] = _config_response(config, request)
    return result
