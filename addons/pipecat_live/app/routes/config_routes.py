"""Status, configuration, integration model discovery, and audio debug routes."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.audio_debug import (
    audio_debug_file_path,
    clear_audio_recordings,
    list_audio_recordings,
)
from app.pipeline.builder import _runtime_flow_errors
from app.services.gemini_models import fetch_integration_models
from app.services.ha_live_bridge import _schedule_ha_assist_warmup
from app.state import (
    ESPHOME_PROVISIONER,
    STARTED_AT,
    STORE,
    UI_CACHE_HEADERS,
    _config_response,
    _offer_path,
    _offer_url,
)

router = APIRouter()


@router.get("/api/assist/status")
async def api_status(request: Request):
    config = STORE.load()
    flow = config.selected_flow(None)
    flow_errors = _runtime_flow_errors(config, flow)
    return {
        "ok": True,
        "uptime_seconds": int(time.time() - STARTED_AT),
        "runner": {
            "host": config.runner_host,
            "port": config.runner_port,
            "offer_url": _offer_url(config, request),
            "offer_path": _offer_path(config),
            "esphome_ws_path": "api/assist/esphome",
        },
        "selected_flow_id": config.selected_flow_id,
        "selected_flow_ready": not flow_errors,
        "selected_flow_errors": flow_errors,
        "flow_count": len(config.flows),
        "mcp_url": config.effective_mcp_url,
        "mcp_token_configured": bool(config.effective_mcp_token),
        "mcp_token_source": config.effective_mcp_token_source,
    }


@router.get("/api/assist/config")
async def api_get_config(request: Request):
    config = STORE.load()
    return _config_response(config, request)


@router.put("/api/assist/config")
async def api_update_config(payload: dict[str, Any], request: Request):
    config = STORE.update_from_public(payload)
    _schedule_ha_assist_warmup(config, reason="config_update")
    ESPHOME_PROVISIONER.request_scan()
    return _config_response(config, request)


@router.get("/api/assist/integrations/{integration_id}/models")
async def api_integration_models(integration_id: str, capability: str = "llm"):
    config = STORE.load()
    integration = config.integration(integration_id)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    return await fetch_integration_models(integration, capability)


@router.post("/api/assist/integrations/{integration_id}/reset")
async def api_reset_integration(integration_id: str, request: Request):
    try:
        config = STORE.reset_integration_defaults(integration_id)
    except KeyError as err:
        raise HTTPException(status_code=404, detail="Integration not found") from err
    return _config_response(config, request)


@router.get("/api/assist/debug/audio")
async def api_audio_debug():
    config = STORE.load()
    return {
        "enabled": config.audio_debug_enabled,
        "keep_sessions": config.audio_debug_keep_sessions,
        "recordings": list_audio_recordings(),
    }


@router.delete("/api/assist/debug/audio")
async def api_clear_audio_debug():
    config = STORE.load()
    clear_audio_recordings()
    return {
        "enabled": config.audio_debug_enabled,
        "keep_sessions": config.audio_debug_keep_sessions,
        "recordings": [],
    }


@router.get("/api/assist/debug/audio/{filename}")
async def api_audio_debug_file(filename: str):
    try:
        path = audio_debug_file_path(filename)
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio debug file not found")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=filename,
        headers=UI_CACHE_HEADERS,
    )
