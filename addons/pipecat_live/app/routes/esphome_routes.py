"""ESPHome satellite WebSocket route."""

from __future__ import annotations

import hmac
import uuid
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger
from pipecat.runner.types import WebSocketRunnerArguments

from app.pipeline.builder import _runtime_flow_errors
from app.pipeline.observers import _va_pipecat_protocol
from app.pipeline.runner import bot
from app.state import STORE, _extract_token

router = APIRouter()


@router.websocket("/api/assist/esphome")
async def api_esphome_satellite(websocket: WebSocket):
    """Run an authenticated raw-PCM Pipecat session for an ESPHome satellite."""

    config = STORE.load()
    secret = config.satellite_shared_secret
    supplied_token = _extract_token(websocket)
    if secret and not hmac.compare_digest(supplied_token, secret):
        logger.warning("ESPHome satellite rejected: invalid or missing token")
        await websocket.close(code=4401)
        return

    flow_id = str(websocket.query_params.get("flow_id") or "").strip()
    flow = config.selected_flow(flow_id)
    flow_errors = _runtime_flow_errors(config, flow)
    if flow_errors:
        await websocket.close(code=4400, reason=flow_errors[0][:120])
        return

    client_id = (
        str(websocket.query_params.get("client_id") or "").strip()
        or str(websocket.client.host if websocket.client else "")
        or f"esphome-{uuid.uuid4().hex[:12]}"
    )
    session_id = str(uuid.uuid4())
    await websocket.accept()
    await websocket.send_text(_va_pipecat_protocol(config).hello())
    logger.info(
        "ESPHome satellite connected client_id={} flow={} session={}",
        client_id,
        flow.id,
        session_id,
    )

    runner_args = WebSocketRunnerArguments(
        websocket=websocket,
        transport_type="websocket",
        session_id=session_id,
        body={
            "client_id": client_id,
            "device_id": client_id,
            "flow_id": flow.id,
            "transport": "va-pipecat",
        },
    )
    # ESPHome keeps this low-latency control connection open from boot. The
    # generic runner's five-minute idle timeout is useful for browser calls but
    # would churn the satellite pipeline and MCP/model warm state.
    runner_args.pipeline_idle_timeout_secs = None
    try:
        await bot(runner_args)
    except WebSocketDisconnect:
        logger.info("ESPHome satellite disconnected client_id={}", client_id)
    except Exception as err:
        logger.exception("ESPHome satellite session failed client_id={}: {}", client_id, err)
        with suppress(Exception):
            payload = _va_pipecat_protocol(config).error_message(
                "session_failed",
                str(err) or err.__class__.__name__,
                recoverable=True,
            )
            await websocket.send_text(payload)
        with suppress(Exception):
            await websocket.close(code=1011)
