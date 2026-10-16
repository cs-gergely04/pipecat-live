"""Pipecat Assist add-on entry point."""

from __future__ import annotations

import argparse
import hmac

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from pipecat.runner.run import app, main as runner_main
from starlette.staticfiles import StaticFiles

from app.pipeline.builder import (
    _build_llm_service,
    _build_stt_service,
    _build_tts_service,
    _gemini_live_service,
    _runtime_flow_errors,
    _runtime_mode,
)
from app.pipeline.flows import _flow_enabled, _flow_node_configs
from app.pipeline.observers import (
    _rtvi_observer_params,
    _should_end_conversation,
    _transport_params,
    _va_pipecat_protocol,
)
from app.pipeline.runner import bot, run_bot
from app.routes.bridge_routes import router as bridge_router
from app.routes.config_routes import router as config_router
from app.routes.esphome_routes import router as esphome_router
from app.routes.mcp_routes import router as mcp_router
from app.services.gemini_models import _static_models_for, fetch_integration_models
from app.services.gemini_stt_tts import (
    _synthesize_tts_audio,
    _transcribe_audio_bytes,
)
from app.services.ha_live_bridge import _schedule_ha_assist_warmup
from app.services.imagen import _run_pipecat_image_generation
from app.services.mcp import detect_ha_mcp_server_addon
from app.state import (
    ESPHOME_PROVISIONER,
    HALiveTurn,
    STARTED_AT,
    STORE,
    SUPERVISOR_URL,
    UI_CACHE_HEADERS,
    UI_DIR,
    _config_response,
    _configure_logging,
    _esphome_ws_path,
    _esphome_ws_url,
    _extract_token,
    _is_offer_path,
    _offer_path,
    _offer_url,
)

app.mount("/assets", StaticFiles(directory=UI_DIR), name="assets")


@app.middleware("http")
async def protect_satellite_offer(request: Request, call_next):
    """Require the shared satellite token for direct SmallWebRTC offers."""

    if _is_offer_path(request.url.path):
        secret = STORE.load().satellite_shared_secret
        if secret and not hmac.compare_digest(_extract_token(request), secret):
            return JSONResponse({"detail": "Invalid satellite token"}, status_code=401)
    try:
        return await call_next(request)
    except Exception as err:
        if _is_offer_path(request.url.path):
            logger.exception("SmallWebRTC offer failed: {}", err)
            return JSONResponse({"detail": str(err) or err.__class__.__name__}, status_code=400)
        raise


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(UI_DIR / "index.html", headers=UI_CACHE_HEADERS)


@app.get("/index.js", include_in_schema=False)
@app.get("/index.css", include_in_schema=False)
@app.get("/logo.svg", include_in_schema=False)
async def ui_asset(request: Request):
    return FileResponse(UI_DIR / request.url.path.lstrip("/"), headers=UI_CACHE_HEADERS)


@app.on_event("startup")
async def _startup_warm_ha_assist() -> None:
    _schedule_ha_assist_warmup(reason="startup")
    ESPHOME_PROVISIONER.start()


@app.on_event("shutdown")
async def _shutdown_esphome_provisioner() -> None:
    await ESPHOME_PROVISIONER.stop()


app.include_router(config_router)
app.include_router(mcp_router)
app.include_router(bridge_router)
app.include_router(esphome_router)


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Pipecat Live")
    runner_main(parser)


__all__ = [
    "ESPHOME_PROVISIONER",
    "HALiveTurn",
    "STARTED_AT",
    "STORE",
    "SUPERVISOR_URL",
    "UI_CACHE_HEADERS",
    "UI_DIR",
    "_build_llm_service",
    "_build_stt_service",
    "_build_tts_service",
    "_config_response",
    "_configure_logging",
    "_esphome_ws_path",
    "_esphome_ws_url",
    "_extract_token",
    "_flow_enabled",
    "_flow_node_configs",
    "_gemini_live_service",
    "_is_offer_path",
    "_offer_path",
    "_offer_url",
    "_rtvi_observer_params",
    "_run_pipecat_image_generation",
    "_runtime_flow_errors",
    "_runtime_mode",
    "_schedule_ha_assist_warmup",
    "_should_end_conversation",
    "_static_models_for",
    "_synthesize_tts_audio",
    "_transcribe_audio_bytes",
    "_transport_params",
    "_va_pipecat_protocol",
    "app",
    "bot",
    "detect_ha_mcp_server_addon",
    "fetch_integration_models",
    "main",
    "run_bot",
]


if __name__ == "__main__":
    main()
