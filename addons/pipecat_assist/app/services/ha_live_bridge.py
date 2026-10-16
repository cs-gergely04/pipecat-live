"""Gemini Live speech-to-speech bridge and warmup for Home Assistant Assist."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from contextlib import suppress
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema

import app.state as _state
from app.config import FlowConfig, IntegrationConfig, RuntimeConfig
from app.mcp_bridge import CombinedMCPBridge
from app.pipeline.builder import (
    _configured_integration,
    _effective_instructions,
    _gemini_model,
    _gemini_voice,
    _merge_tools_schema,
    _model_name,
    _runtime_mode,
    _web_search_tool_schema,
)
from app.pipeline.observers import _should_end_conversation
from app.services.gemini_stt_tts import (
    _mime_param,
    _pcm_stream_payload,
    _send_ws_json,
    _wav_from_pcm,
    _websocket_connect,
    _ws_metadata_value,
)
from app.services.google_credentials import _integration_api_key
from app.state import (
    DEFAULT_HA_STT_CHANNELS,
    DEFAULT_HA_STT_SAMPLE_RATE,
    DEFAULT_HA_STT_SAMPLE_WIDTH,
    HA_LIVE_RESULT_TIMEOUT_SECONDS,
    HA_LIVE_TRANSCRIPT_TIMEOUT_SECONDS,
    HA_LIVE_TURN_TTL_SECONDS,
    HA_LIVE_TURNS_BY_SPEECH,
    HA_LIVE_TURNS_BY_TRANSCRIPT,
    HALiveTurn,
    STORE,
    _text_fingerprint,
)


def _normalized_turn_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _ha_live_turn_key(flow_id: str, text: str) -> tuple[str, str]:
    return flow_id, _normalized_turn_text(text)


def _prune_ha_live_turns() -> None:
    now = time.time()
    for table in (HA_LIVE_TURNS_BY_TRANSCRIPT, HA_LIVE_TURNS_BY_SPEECH):
        for key, turn in list(table.items()):
            if now - turn.started_at > HA_LIVE_TURN_TTL_SECONDS:
                table.pop(key, None)


def _remember_ha_live_transcript(turn: HALiveTurn) -> None:
    transcript = turn.transcript.strip()
    if not transcript:
        return
    _prune_ha_live_turns()
    HA_LIVE_TURNS_BY_TRANSCRIPT[_ha_live_turn_key(turn.flow_id, transcript)] = turn


def _remember_ha_live_speech(turn: HALiveTurn) -> None:
    speech = turn.speech.strip()
    if not speech:
        return
    _prune_ha_live_turns()
    HA_LIVE_TURNS_BY_SPEECH[_ha_live_turn_key(turn.flow_id, speech)] = turn


def _find_ha_live_turn_by_transcript(flow_id: str, text: str) -> HALiveTurn | None:
    _prune_ha_live_turns()
    return HA_LIVE_TURNS_BY_TRANSCRIPT.get(_ha_live_turn_key(flow_id, text))


def _find_ha_live_turn_by_speech(flow_id: str, text: str) -> HALiveTurn | None:
    _prune_ha_live_turns()
    return HA_LIVE_TURNS_BY_SPEECH.get(_ha_live_turn_key(flow_id, text))


def _ha_live_bridge_integration(
    config: RuntimeConfig,
    flow: FlowConfig,
    metadata: dict[str, Any],
) -> IntegrationConfig | None:
    """Return Gemini Live when the active pipeline can be wrapped for HA Assist."""

    integration = config.model_integration(flow)
    provider_kind = integration.kind if integration else flow.provider_id
    if _runtime_mode(flow, provider_kind) != "s2s" or provider_kind != "gemini":
        return None
    if not _configured_integration(integration):
        return None
    if not ((integration.api_key if integration else "") or os.getenv("GOOGLE_API_KEY", "")):
        return None

    codec = str(_ws_metadata_value(metadata, "codec", "pcm") or "pcm").lower()
    sample_rate = int(
        _ws_metadata_value(metadata, "sample_rate", DEFAULT_HA_STT_SAMPLE_RATE)
        or DEFAULT_HA_STT_SAMPLE_RATE
    )
    bit_rate = int(
        _ws_metadata_value(metadata, "bit_rate", DEFAULT_HA_STT_SAMPLE_WIDTH * 8)
        or DEFAULT_HA_STT_SAMPLE_WIDTH * 8
    )
    channels = int(_ws_metadata_value(metadata, "channel", DEFAULT_HA_STT_CHANNELS) or DEFAULT_HA_STT_CHANNELS)
    if bit_rate != 16 or channels != 1 or "pcm" not in codec:
        logger.info(
            "Skipping Pipecat Live Bridge for flow {} because HA audio is {} Hz, {} bit, {} channel, codec={}",
            flow.id,
            sample_rate,
            bit_rate,
            channels,
            codec,
        )
        return None
    return integration


def _gemini_live_tool_declarations(tools_schema: ToolsSchema) -> dict[str, Any]:
    declarations: list[dict[str, Any]] = []
    for tool in tools_schema.standard_tools:
        declarations.append(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": {
                    "type": "object",
                    "properties": tool.properties,
                    "required": tool.required,
                },
            }
        )
    return {"functionDeclarations": declarations}


def _gemini_live_ha_setup(
    config: RuntimeConfig,
    flow: FlowConfig,
    integration: IntegrationConfig,
    tools_schema: ToolsSchema | None,
) -> dict[str, Any]:
    setup: dict[str, Any] = {
        "model": _gemini_model(_model_name(flow, integration, "gemini")),
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": _gemini_voice(flow, integration),
                    }
                }
            },
        },
        "systemInstruction": {
            "parts": [
                {
                    "text": (
                        f"{_effective_instructions(flow)}\n\n"
                        "You are running inside Home Assistant Assist through Pipecat Live Bridge. "
                        "Use Home Assistant tools silently for explicit smart-home requests, "
                        "then answer briefly and naturally."
                    )
                }
            ]
        },
        "inputAudioTranscription": {},
        "outputAudioTranscription": {},
        "realtimeInputConfig": {
            "automaticActivityDetection": {
                "disabled": True,
            }
        },
    }
    if flow.max_output_tokens:
        setup["generationConfig"]["maxOutputTokens"] = flow.max_output_tokens
    if tools_schema and tools_schema.standard_tools:
        setup["tools"] = [_gemini_live_tool_declarations(tools_schema)]
    return setup


def _gemini_live_error_message(message: dict[str, Any]) -> str:
    error = message.get("error") if isinstance(message, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("status") or error)
    return str(error or "")


async def _warm_mcp_tools_schema(
    config: RuntimeConfig,
    flow: FlowConfig,
) -> ToolsSchema | None:
    mcp_servers = config.enabled_mcp_servers(config.effective_mcp_token) if flow.mcp_enabled else []
    if not mcp_servers:
        return None
    bridge = CombinedMCPBridge(mcp_servers, flow.mcp_tool_allowlist)
    try:
        await bridge.start()
        tools = await bridge.tools_schema(
            cache_enabled=config.mcp_tools_cache_enabled,
            cache_ttl_seconds=config.mcp_tools_cache_ttl_seconds,
            refresh=False,
        )
        logger.info(
            "HA Assist warmup cached MCP schema flow={} tools={}",
            flow.id,
            len(tools.standard_tools),
        )
        return tools if tools.standard_tools else None
    finally:
        with suppress(Exception):
            await bridge.close()


async def _warm_gemini_live_ha_setup(
    config: RuntimeConfig,
    flow: FlowConfig,
    integration: IntegrationConfig,
    tools_schema: ToolsSchema | None,
) -> None:
    api_key = _integration_api_key(
        integration,
        "Pipecat Live Bridge warmup",
        os.getenv("GOOGLE_API_KEY", ""),
    )
    url = (
        "wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
        f"?key={quote(api_key, safe='')}"
    )
    started_at = time.perf_counter()
    async with _websocket_connect(url, {}) as provider_ws:
        await provider_ws.send(
            json.dumps({"setup": _gemini_live_ha_setup(config, flow, integration, tools_schema)})
        )
        async with asyncio.timeout(10):
            while True:
                raw = await provider_ws.recv()
                message = json.loads(raw)
                if message.get("setupComplete") is not None:
                    break
                if message.get("error"):
                    raise RuntimeError(_gemini_live_error_message(message))
    logger.info(
        "HA Assist warmup completed Pipecat Live Bridge setup flow={} model={} duration_ms={:.0f}",
        flow.id,
        _model_name(flow, integration, "gemini"),
        (time.perf_counter() - started_at) * 1000,
    )


async def _warm_ha_assist_flow(config: RuntimeConfig, flow: FlowConfig, reason: str) -> None:
    started_at = time.perf_counter()
    try:
        mcp_tools_schema = None
        try:
            mcp_tools_schema = await _warm_mcp_tools_schema(config, flow)
        except Exception as err:
            logger.debug("HA Assist MCP warmup skipped/failed for flow {}: {}", flow.id, err)
        local_tool_schemas = [schema for schema in [_web_search_tool_schema(config, flow)] if schema]
        tools_schema = _merge_tools_schema(mcp_tools_schema, local_tool_schemas)
        integration = _ha_live_bridge_integration(
            config,
            flow,
            {
                "codec": "pcm",
                "sample_rate": DEFAULT_HA_STT_SAMPLE_RATE,
                "bit_rate": DEFAULT_HA_STT_SAMPLE_WIDTH * 8,
                "channel": DEFAULT_HA_STT_CHANNELS,
            },
        )
        if integration:
            await _warm_gemini_live_ha_setup(config, flow, integration, tools_schema)
        logger.info(
            "HA Assist warmup finished flow={} reason={} duration_ms={:.0f}",
            flow.id,
            reason,
            (time.perf_counter() - started_at) * 1000,
        )
    except asyncio.CancelledError:
        raise
    except Exception as err:
        logger.debug("HA Assist warmup skipped/failed for flow {}: {}", flow.id, err)


def _schedule_ha_assist_warmup(
    config: RuntimeConfig | None = None,
    *,
    reason: str = "startup",
) -> None:
    try:
        config = config or STORE.load()
        flow = config.selected_flow(None)
    except Exception as err:
        logger.debug("HA Assist warmup could not load configuration: {}", err)
        return

    if _state.HA_ASSIST_WARMUP_TASK and not _state.HA_ASSIST_WARMUP_TASK.done():
        _state.HA_ASSIST_WARMUP_TASK.cancel()
    _state.HA_ASSIST_WARMUP_TASK = asyncio.create_task(_warm_ha_assist_flow(config, flow, reason))

    def _log_result(task: asyncio.Task) -> None:
        with suppress(asyncio.CancelledError):
            if err := task.exception():
                logger.debug("HA Assist warmup task failed: {}", err)

    _state.HA_ASSIST_WARMUP_TASK.add_done_callback(_log_result)


class _LocalFunctionParams:
    def __init__(self, arguments: dict[str, Any]):
        self.arguments = arguments
        self.result = ""

    async def result_callback(self, result: Any) -> None:
        self.result = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


async def _call_local_live_tool(schema: FunctionSchema, arguments: dict[str, Any]) -> str:
    handler = getattr(schema, "handler", None)
    if not handler:
        return "Tool has no local handler."
    params = _LocalFunctionParams(arguments)
    await handler(params)
    return params.result or "Tool completed."


async def _call_gemini_live_tool(
    *,
    function_call: dict[str, Any],
    bridge: CombinedMCPBridge | None,
    local_tools: dict[str, FunctionSchema],
    mcp_tool_names: set[str],
) -> dict[str, Any]:
    name = str(function_call.get("name") or "")
    call_id = str(function_call.get("id") or "")
    args = function_call.get("args")
    arguments = args if isinstance(args, dict) else {}
    try:
        if name in local_tools:
            result = await _call_local_live_tool(local_tools[name], arguments)
        elif bridge and name in mcp_tool_names:
            result = await bridge.call_tool(name, arguments)
        else:
            result = f"Unknown tool: {name}"
        return {"id": call_id, "name": name, "response": {"result": result}}
    except Exception as err:
        logger.warning("Pipecat Live Bridge tool {} failed: {}", name, err)
        return {"id": call_id, "name": name, "response": {"error": str(err)}}


def _append_gemini_live_text(existing: str, chunk: str) -> str:
    if not chunk:
        return existing
    if not existing:
        return chunk
    if chunk.startswith(existing):
        return chunk
    if existing.endswith(chunk):
        return existing
    if existing.endswith((" ", "\n")) or chunk.startswith((" ", "\n", ".", ",", "!", "?", ":", ";")):
        return f"{existing}{chunk}"
    if len(chunk) > 2 and existing[-1].isalnum() and chunk[0].isalnum():
        return f"{existing} {chunk}"
    return f"{existing}{chunk}"


async def _handle_gemini_live_message(
    *,
    websocket: WebSocket | None,
    provider_ws,
    message: dict[str, Any],
    turn: HALiveTurn,
    bridge: CombinedMCPBridge | None,
    local_tools: dict[str, FunctionSchema],
    mcp_tool_names: set[str],
) -> str:
    if message.get("error"):
        raise RuntimeError(_gemini_live_error_message(message))

    tool_call = message.get("toolCall")
    if isinstance(tool_call, dict):
        function_calls = tool_call.get("functionCalls") or []
        responses = [
            await _call_gemini_live_tool(
                function_call=call,
                bridge=bridge,
                local_tools=local_tools,
                mcp_tool_names=mcp_tool_names,
            )
            for call in function_calls
            if isinstance(call, dict)
        ]
        if responses:
            await provider_ws.send(json.dumps({"toolResponse": {"functionResponses": responses}}))

    server_content = message.get("serverContent")
    if not isinstance(server_content, dict):
        return ""

    input_transcription = server_content.get("inputTranscription")
    if isinstance(input_transcription, dict):
        text = str(input_transcription.get("text") or "")
        if text:
            turn.transcript = _append_gemini_live_text(turn.transcript, text)
            if websocket:
                await _send_ws_json(websocket, {"type": "partial", "text": turn.transcript.strip()})

    output_transcription = server_content.get("outputTranscription")
    if isinstance(output_transcription, dict):
        text = str(output_transcription.get("text") or "")
        if text:
            turn.speech = _append_gemini_live_text(turn.speech, text)

    model_turn = server_content.get("modelTurn")
    parts = model_turn.get("parts") if isinstance(model_turn, dict) else []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        text = str(part.get("text") or "")
        if text and not output_transcription:
            turn.speech = _append_gemini_live_text(turn.speech, text)
        inline = part.get("inlineData") or part.get("inline_data")
        if isinstance(inline, dict) and inline.get("data"):
            media_type = str(inline.get("mimeType") or inline.get("mime_type") or "audio/pcm;rate=24000")
            turn.audio_sample_rate = int(
                _mime_param(media_type, "rate") or _mime_param(media_type, "sample_rate") or 24000
            )
            turn.audio_chunks.append(base64.b64decode(str(inline["data"])))

    if server_content.get("generationComplete"):
        return "generation_complete"
    if server_content.get("turnComplete"):
        return "turn_complete"
    return ""


async def _send_gemini_live_audio_queue(
    provider_ws,
    queue: "asyncio.Queue[bytes | None]",
    *,
    sample_rate: int,
) -> None:
    await provider_ws.send(json.dumps({"realtimeInput": {"activityStart": {}}}))
    while True:
        chunk = await queue.get()
        if chunk is None:
            break
        payload = _pcm_stream_payload(chunk)
        if not payload:
            continue
        await provider_ws.send(
            json.dumps(
                {
                    "realtimeInput": {
                        "audio": {
                            "data": base64.b64encode(payload).decode("ascii"),
                            "mimeType": f"audio/pcm;rate={sample_rate or DEFAULT_HA_STT_SAMPLE_RATE}",
                        }
                    }
                }
            )
        )
    await provider_ws.send(json.dumps({"realtimeInput": {"activityEnd": {}}}))


async def _run_gemini_live_ha_turn(
    *,
    turn: HALiveTurn,
    queue: "asyncio.Queue[bytes | None]",
    websocket: WebSocket | None,
    config: RuntimeConfig,
    flow: FlowConfig,
    integration: IntegrationConfig,
    input_sample_rate: int,
) -> None:
    started_at = time.perf_counter()
    bridge: CombinedMCPBridge | None = None
    mcp_tools_schema: ToolsSchema | None = None
    try:
        local_tool_schemas = [schema for schema in [_web_search_tool_schema(config, flow)] if schema]
        local_tools = {schema.name: schema for schema in local_tool_schemas}
        mcp_servers = config.enabled_mcp_servers(config.effective_mcp_token) if flow.mcp_enabled else []
        if mcp_servers:
            bridge = CombinedMCPBridge(mcp_servers, flow.mcp_tool_allowlist)
            try:
                await bridge.start()
                mcp_tools_schema = await bridge.tools_schema(
                    cache_enabled=config.mcp_tools_cache_enabled,
                    cache_ttl_seconds=config.mcp_tools_cache_ttl_seconds,
                )
            except Exception as err:
                with suppress(Exception):
                    await bridge.close()
                raise RuntimeError(f"MCP tools are enabled but unavailable: {err}") from err
        tools_schema = _merge_tools_schema(mcp_tools_schema, local_tool_schemas)
        mcp_tool_names = {
            tool.name for tool in (mcp_tools_schema.standard_tools if mcp_tools_schema else [])
        }
        api_key = _integration_api_key(
            integration,
            "Pipecat Live Bridge",
            os.getenv("GOOGLE_API_KEY", ""),
        )
        url = (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
            f"?key={quote(api_key, safe='')}"
        )
        async with _websocket_connect(url, {}) as provider_ws:
            await provider_ws.send(
                json.dumps({"setup": _gemini_live_ha_setup(config, flow, integration, tools_schema)})
            )
            async with asyncio.timeout(10):
                while True:
                    raw = await provider_ws.recv()
                    message = json.loads(raw)
                    if message.get("setupComplete") is not None:
                        break
                    if message.get("error"):
                        raise RuntimeError(_gemini_live_error_message(message))

            sender_task = asyncio.create_task(
                _send_gemini_live_audio_queue(provider_ws, queue, sample_rate=input_sample_rate)
            )
            completion_reason = ""
            try:
                async with asyncio.timeout(HA_LIVE_RESULT_TIMEOUT_SECONDS):
                    async for raw in provider_ws:
                        message = json.loads(raw)
                        completion_reason = await _handle_gemini_live_message(
                            websocket=websocket,
                            provider_ws=provider_ws,
                            message=message,
                            turn=turn,
                            bridge=bridge,
                            local_tools=local_tools,
                            mcp_tool_names=mcp_tool_names,
                        )
                        if completion_reason:
                            break
            finally:
                sender_task.cancel()
                with suppress(Exception):
                    await sender_task
        _remember_ha_live_transcript(turn)
        _remember_ha_live_speech(turn)
        turn.transcript_ready.set()
        logger.info(
            "Pipecat Live Bridge turn finished flow={} completion={} transcript={} speech={} audio_bytes={} duration_ms={:.0f}",
            flow.id,
            completion_reason or "unknown",
            _text_fingerprint(turn.transcript),
            _text_fingerprint(turn.speech),
            len(turn.audio),
            (time.perf_counter() - started_at) * 1000,
        )
    except Exception as err:
        turn.error = str(err)
        logger.warning("Pipecat Live Bridge failed for flow {}: {}", flow.id, err)
    finally:
        if not turn.transcript_ready.is_set():
            turn.transcript_ready.set()
        turn.done.set()
        if bridge:
            with suppress(Exception):
                await bridge.close()


def _start_gemini_live_ha_turn(
    *,
    config: RuntimeConfig,
    flow: FlowConfig,
    integration: IntegrationConfig,
    websocket: WebSocket | None,
    input_sample_rate: int,
) -> tuple[HALiveTurn, "asyncio.Queue[bytes | None]"]:
    turn = HALiveTurn(flow_id=flow.id, provider="gemini-live")
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=32)
    turn.task = asyncio.create_task(
        _run_gemini_live_ha_turn(
            turn=turn,
            queue=queue,
            websocket=websocket,
            config=config,
            flow=flow,
            integration=integration,
            input_sample_rate=input_sample_rate,
        )
    )
    return turn, queue


async def _wait_for_ha_live_transcript(turn: HALiveTurn) -> str:
    with suppress(TimeoutError):
        async with asyncio.timeout(HA_LIVE_TRANSCRIPT_TIMEOUT_SECONDS):
            await turn.transcript_ready.wait()
    return turn.transcript.strip()


async def _ha_live_conversation_result(
    turn: HALiveTurn,
    *,
    conversation_id: str | None,
) -> dict[str, Any] | None:
    async with asyncio.timeout(HA_LIVE_RESULT_TIMEOUT_SECONDS):
        await turn.done.wait()
    if turn.error or not turn.speech.strip():
        return None
    _remember_ha_live_speech(turn)
    end_conversation = _should_end_conversation(turn.transcript, turn.speech)
    return {
        "speech": turn.speech.strip(),
        "conversation_id": conversation_id,
        "continue_conversation": not end_conversation,
        "end_conversation": end_conversation,
        "error": "",
        "source": "pipecat_live_bridge",
    }


async def _ha_live_tts_audio(turn: HALiveTurn) -> tuple[bytes, str, str] | None:
    async with asyncio.timeout(HA_LIVE_RESULT_TIMEOUT_SECONDS):
        await turn.done.wait()
    if turn.error or not turn.audio:
        return None
    return (
        _wav_from_pcm(
            turn.audio,
            sample_rate=turn.audio_sample_rate or 24000,
            sample_width=2,
            channels=1,
        ),
        "audio/wav",
        "wav",
    )


async def _handle_ha_live_stt_stream(
    *,
    websocket: WebSocket,
    config: RuntimeConfig,
    flow: FlowConfig,
    integration: IntegrationConfig,
    metadata: dict[str, Any],
    content_type: str,
) -> None:
    turn, live_queue = _start_gemini_live_ha_turn(
        config=config,
        flow=flow,
        integration=integration,
        websocket=websocket,
        input_sample_rate=int(
            _ws_metadata_value(metadata, "sample_rate", DEFAULT_HA_STT_SAMPLE_RATE)
            or DEFAULT_HA_STT_SAMPLE_RATE
        ),
    )
    chunks: list[bytes] = []
    await websocket.send_json({"type": "ready", "mode": "live", "provider": "gemini"})
    try:
        while True:
            message = await websocket.receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                raise WebSocketDisconnect()
            if data := message.get("bytes"):
                chunks.append(data)
                await live_queue.put(data)
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
    finally:
        with suppress(Exception):
            await live_queue.put(None)

    transcript = await _wait_for_ha_live_transcript(turn)
    if turn.error:
        raise HTTPException(status_code=502, detail=f"Pipecat Live Bridge failed: {turn.error}")
    if not transcript:
        logger.info("Pipecat Live Bridge returned no transcript for flow {}; treating as no speech", flow.id)
        await websocket.send_json({"type": "final", "text": ""})
        await websocket.close()
        return

    await websocket.send_json({"type": "final", "text": transcript.strip()})
    await websocket.close()
