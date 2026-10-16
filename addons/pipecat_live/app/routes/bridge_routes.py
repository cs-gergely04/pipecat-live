"""Home Assistant Assist conversation, AI Task, STT, and TTS bridge routes."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from loguru import logger

from app.pipeline.builder import _step_model_for
from app.pipeline.observers import _should_end_conversation
from app.services.gemini_stt_tts import (
    _bridge_unavailable,
    _ha_assist_step_integration,
    _ha_stt_model_fallback,
    _ha_supported_stt_names,
    _parse_speech_content,
    _pop_tts_prefetch,
    _preferred_tts_format,
    _prune_tts_prefetch,
    _send_ws_json,
    _start_tts_prefetch,
    _stt_metadata_from_start,
    _streaming_stt_kind,
    _streaming_transcribe_audio,
    _synthesize_tts_audio,
    _transcribe_audio_bytes,
)
from app.services.ha_live_bridge import (
    _find_ha_live_turn_by_speech,
    _find_ha_live_turn_by_transcript,
    _ha_live_bridge_integration,
    _ha_live_conversation_result,
    _ha_live_tts_audio,
    _handle_ha_live_stt_stream,
)
from app.services.google_credentials import _provider_call, _require_integration
from app.services.imagen import (
    _ai_image_task_prompt,
    _ai_task_prompt,
    _enabled_image_generation_integration,
    _json_from_model_text,
    _run_pipecat_image_generation,
)
from app.state import HA_STT_BRIDGE_KINDS, STORE, _text_fingerprint
from app.text_agent import run_text_conversation

router = APIRouter()


@router.post("/api/assist/conversation")
async def api_conversation(payload: dict[str, Any]):
    started_at = time.perf_counter()
    text = str(payload.get("text", "")).strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    config = STORE.load()
    flow = config.selected_flow(payload.get("flow_id"))
    live_turn = _find_ha_live_turn_by_transcript(flow.id, text)
    if live_turn:
        try:
            live_result = await _ha_live_conversation_result(
                live_turn,
                conversation_id=payload.get("conversation_id"),
            )
        except Exception as err:
            raise HTTPException(
                status_code=502,
                detail=f"Pipecat Live Bridge conversation failed: {err}",
            ) from err
        if live_result:
            end_conversation = _should_end_conversation(
                text,
                str(live_result.get("speech") or ""),
            )
            live_result["end_conversation"] = end_conversation
            live_result["continue_conversation"] = not end_conversation
            logger.info(
                "HA Assist conversation served from Pipecat Live Bridge flow={} input={} speech={} continue={} total_ms={:.0f}",
                flow.id,
                _text_fingerprint(text),
                _text_fingerprint(str(live_result.get("speech") or "")),
                live_result["continue_conversation"],
                (time.perf_counter() - started_at) * 1000,
            )
            return live_result
        raise HTTPException(status_code=502, detail="Pipecat Live Bridge conversation returned no speech")

    async def request():
        result = await run_text_conversation(
            config,
            text=text,
            language=payload.get("language"),
            conversation_id=payload.get("conversation_id"),
            flow_id=flow.id,
            mcp_token=config.effective_mcp_token,
        )
        end_conversation = _should_end_conversation(text, str(result.get("speech") or ""))
        result["end_conversation"] = end_conversation
        result["continue_conversation"] = (
            not bool(result.get("error"))
            and not end_conversation
        )
        if not result.get("error"):
            _start_tts_prefetch(
                config=config,
                flow=flow,
                text=str(result.get("speech") or ""),
                language=payload.get("language"),
            )
        logger.info(
            "HA Assist conversation served flow={} input={} speech={} error={} total_ms={:.0f}",
            flow.id,
            _text_fingerprint(text),
            _text_fingerprint(str(result.get("speech") or "")),
            result.get("error") or "",
            (time.perf_counter() - started_at) * 1000,
        )
        return result

    return await _provider_call("HA Assist conversation", request, attempts=1)


@router.post("/api/assist/ai-task")
async def api_ai_task(payload: dict[str, Any]):
    """Run a Home Assistant AI Task through the selected Pipecat Assist text model."""

    config = STORE.load()
    flow = config.selected_flow(payload.get("flow_id"))
    text = _ai_task_prompt(payload)
    if not text.strip():
        raise HTTPException(status_code=400, detail="instructions are required")

    result = await run_text_conversation(
        config,
        text=text,
        language=payload.get("language"),
        conversation_id=payload.get("conversation_id"),
        flow_id=flow.id,
        mcp_token=config.effective_mcp_token,
    )
    if result.get("error"):
        raise HTTPException(status_code=502, detail=result.get("speech") or result.get("error"))

    speech = str(result.get("speech") or "").strip()
    data: Any = speech
    if payload.get("structured"):
        try:
            data = _json_from_model_text(speech)
        except ValueError as err:
            raise HTTPException(status_code=502, detail=str(err)) from err

    return {
        "conversation_id": result.get("conversation_id") or payload.get("conversation_id"),
        "data": data,
    }


@router.post("/api/assist/ai-task/image")
async def api_ai_task_image(payload: dict[str, Any]):
    """Run a Home Assistant AI Task image request through a Pipecat image service."""

    prompt = _ai_image_task_prompt(payload)
    if not prompt:
        raise HTTPException(status_code=400, detail="instructions are required")

    config = STORE.load()
    integration = _enabled_image_generation_integration(config, payload)
    started_at = time.perf_counter()
    result = await _run_pipecat_image_generation(config, integration, prompt)
    logger.info(
        "HA AI Task image generated provider={} model={} prompt={} bytes={} total_ms={:.0f}",
        integration.kind,
        result.get("model") or "",
        _text_fingerprint(prompt),
        len(result["image"]),
        (time.perf_counter() - started_at) * 1000,
    )
    return {
        "conversation_id": payload.get("conversation_id"),
        "image_base64": base64.b64encode(result["image"]).decode("ascii"),
        "mime_type": result["mime_type"],
        "width": result.get("width"),
        "height": result.get("height"),
        "model": result.get("model"),
        "revised_prompt": result.get("revised_prompt"),
        "source_url": result.get("source_url"),
    }


@router.post("/api/assist/stt")
async def api_stt(request: Request, flow_id: str | None = None):
    """Best-effort STT bridge for the classic Home Assistant Assist pipeline."""

    config = STORE.load()
    flow = config.selected_flow(flow_id)
    metadata = _parse_speech_content(request.headers.get("x-speech-content", ""))
    content_type = request.headers.get("content-type") or "audio/wav"
    return await _transcribe_audio_bytes(
        config=config,
        flow=flow,
        audio=await request.body(),
        metadata=metadata,
        content_type=content_type,
    )


@router.websocket("/api/assist/stt/stream")
async def api_stt_stream(websocket: WebSocket):
    """Streaming STT bridge for the classic Home Assistant Assist pipeline."""

    await websocket.accept()
    provider_task: asyncio.Task[str] | None = None
    provider_queue: asyncio.Queue[bytes | None] | None = None
    chunks: list[bytes] = []
    metadata: dict[str, Any] = {}
    content_type = "audio/wav"
    config = STORE.load()
    flow = config.selected_flow(None)

    try:
        start = await websocket.receive_json()
        if not isinstance(start, dict) or start.get("type") != "start":
            await websocket.send_json({"type": "error", "detail": "Expected start message"})
            await websocket.close(code=1003)
            return

        flow = config.selected_flow(start.get("flow_id"))
        metadata = _stt_metadata_from_start(start)
        content_type = str(start.get("content_type") or "audio/wav")
        logger.info(
            "HA Assist STT stream started flow={} content_type={} metadata={}",
            flow.id,
            content_type,
            metadata,
        )
        live_integration = _ha_live_bridge_integration(config, flow, metadata)
        if live_integration:
            logger.info("HA Assist STT using Pipecat Live Bridge for flow {}", flow.id)
            await _handle_ha_live_stt_stream(
                websocket=websocket,
                config=config,
                flow=flow,
                integration=live_integration,
                metadata=metadata,
                content_type=content_type,
            )
            return

        step, integration = _ha_assist_step_integration(config, flow, "stt", HA_STT_BRIDGE_KINDS)
        if not integration:
            raise _bridge_unavailable("STT", _ha_supported_stt_names())
        integration = _require_integration(integration, "STT", fields=())
        model = _step_model_for(step, integration, "stt", _ha_stt_model_fallback(integration))
        streaming_kind = _streaming_stt_kind(integration, model)
        logger.info(
            "HA Assist STT selected flow={} integration={} kind={} model={} mode={}",
            flow.id,
            integration.name,
            integration.kind,
            model,
            streaming_kind or "buffered",
        )

        if streaming_kind:
            provider_queue = asyncio.Queue(maxsize=32)
            provider_task = asyncio.create_task(
                _streaming_transcribe_audio(
                    websocket=websocket,
                    queue=provider_queue,
                    config=config,
                    flow=flow,
                    step=step,
                    integration=integration,
                    model=model,
                    metadata=metadata,
                )
            )
            await websocket.send_json({"type": "ready", "mode": "streaming", "provider": integration.kind})
        else:
            await websocket.send_json({"type": "ready", "mode": "buffered", "provider": integration.kind})

        while True:
            message = await websocket.receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                raise WebSocketDisconnect()
            if data := message.get("bytes"):
                chunks.append(data)
                if provider_queue and provider_task and not provider_task.done():
                    await provider_queue.put(data)
                continue
            raw_text = message.get("text")
            if not raw_text:
                continue
            try:
                event = json.loads(raw_text)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "end":
                break

        audio = b"".join(chunks)
        logger.info(
            "HA Assist STT stream received flow={} chunks={} bytes={}",
            flow.id,
            len(chunks),
            len(audio),
        )

        if provider_queue and provider_task and not provider_task.done():
            await provider_queue.put(None)

        transcript = ""
        if provider_task:
            try:
                transcript = await provider_task
            except Exception as err:
                raise HTTPException(
                    status_code=502,
                    detail=f"{integration.name} streaming STT failed: {err}",
                ) from err

        if not transcript:
            if streaming_kind:
                logger.info(
                    "HA Assist streaming STT returned no transcript flow={} integration={}; treating as no speech",
                    flow.id,
                    integration.name,
                )
            else:
                result = await _transcribe_audio_bytes(
                    config=config,
                    flow=flow,
                    audio=audio,
                    metadata=metadata,
                    content_type=content_type,
                )
                transcript = result.get("text", "")

        logger.info(
            "HA Assist STT stream finished flow={} transcript={}",
            flow.id,
            _text_fingerprint(transcript),
        )
        await websocket.send_json({"type": "final", "text": transcript.strip()})
        await websocket.close()
    except WebSocketDisconnect:
        logger.info("HA Assist STT stream disconnected flow={}", flow.id)
        if provider_queue:
            with suppress(Exception):
                await provider_queue.put(None)
        if provider_task:
            provider_task.cancel()
    except HTTPException as err:
        await _send_ws_json(websocket, {"type": "error", "detail": err.detail})
        with suppress(Exception):
            await websocket.close(code=1011)
    except Exception as err:
        logger.exception("HA Assist streaming STT failed: {}", err)
        await _send_ws_json(websocket, {"type": "error", "detail": str(err)})
        with suppress(Exception):
            await websocket.close(code=1011)


@router.post("/api/assist/tts")
async def api_tts(payload: dict[str, Any]):
    """Best-effort TTS bridge for the classic Home Assistant Assist pipeline."""

    started_at = time.perf_counter()
    config = STORE.load()
    flow = config.selected_flow(payload.get("flow_id"))
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text was provided")

    response_format = _preferred_tts_format(payload)
    live_turn = _find_ha_live_turn_by_speech(flow.id, text)
    if live_turn:
        try:
            live_audio = await _ha_live_tts_audio(live_turn)
        except Exception as err:
            raise HTTPException(
                status_code=502,
                detail=f"Pipecat Live Bridge TTS failed: {err}",
            ) from err
        if live_audio:
            audio, media_type, extension = live_audio
            logger.info(
                "HA Assist TTS served from Pipecat Live Bridge flow={} text={} requested_format={} output={} bytes={} total_ms={:.0f}",
                flow.id,
                _text_fingerprint(text),
                response_format,
                extension,
                len(audio),
                (time.perf_counter() - started_at) * 1000,
            )
            return Response(content=audio, media_type=media_type, headers={"X-Audio-Extension": extension})
        raise HTTPException(status_code=502, detail="Pipecat Live Bridge TTS returned no audio")

    _prune_tts_prefetch()
    cached = _pop_tts_prefetch(flow.id, text, response_format)
    cache_status = "miss"
    prefetch_age_ms = 0.0
    wait_started_at = time.perf_counter()
    if cached:
        cache_status, created_at, task = cached
        prefetch_age_ms = max(0.0, (time.time() - created_at) * 1000)
        try:
            audio, media_type, extension = await task
        except Exception as err:
            logger.debug("Prefetched TTS failed; synthesizing on demand: {}", err)
            cache_status = f"{cache_status}-failed"
            audio, media_type, extension = await _synthesize_tts_audio(
                config=config,
                flow=flow,
                text=text,
                payload=payload,
            )
    else:
        audio, media_type, extension = await _synthesize_tts_audio(
            config=config,
            flow=flow,
            text=text,
            payload=payload,
        )
    logger.info(
        "HA Assist TTS served flow={} text={} cache={} prefetch_age_ms={:.0f} requested_format={} output={} bytes={} wait_ms={:.0f} total_ms={:.0f}",
        flow.id,
        _text_fingerprint(text),
        cache_status,
        prefetch_age_ms,
        response_format,
        extension,
        len(audio),
        (time.perf_counter() - wait_started_at) * 1000,
        (time.perf_counter() - started_at) * 1000,
    )
    return Response(content=audio, media_type=media_type, headers={"X-Audio-Extension": extension})
