"""Gemini REST / Google Cloud STT & TTS synthesis, WAV/PCM conversion, and TTS prefetch."""

from __future__ import annotations

import asyncio
import base64
import os
import time
import wave
from contextlib import suppress
from io import BytesIO
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request, WebSocket
from loguru import logger

from app.config import (
    DEFAULT_GEMINI_LIVE_VOICE,
    DEFAULT_GEMINI_TEXT_MODEL,
    DEFAULT_GEMINI_TTS_MODEL,
    DEFAULT_GOOGLE_TTS_VOICE,
    GEMINI_TTS_FALLBACK_MODELS,
    FlowConfig,
    IntegrationConfig,
    RuntimeConfig,
)
from app.pipeline.builder import (
    _runtime_language,
    _runtime_speed,
    _step_integration,
    _step_model_for,
    _step_voice,
)
from app.services.google_credentials import (
    _google_cloud_credentials,
    _integration_api_key_or_400,
    _provider_call,
    _require_integration,
)
from app.state import (
    AUDIO_TTS_FORMATS,
    DEFAULT_HA_STT_CHANNELS,
    DEFAULT_HA_STT_SAMPLE_RATE,
    DEFAULT_HA_STT_SAMPLE_WIDTH,
    HA_STT_BRIDGE_KINDS,
    HA_TTS_BRIDGE_KINDS,
    TTS_PREFETCH,
    TTS_PREFETCH_TTL_SECONDS,
    _text_fingerprint,
)


def _parse_speech_content(value: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for entry in value.split(";"):
        key, _, raw = entry.strip().partition("=")
        if key:
            fields[key] = raw.strip()
    return fields


def _wav_from_pcm(
    audio: bytes,
    *,
    sample_rate: int = DEFAULT_HA_STT_SAMPLE_RATE,
    sample_width: int = DEFAULT_HA_STT_SAMPLE_WIDTH,
    channels: int = DEFAULT_HA_STT_CHANNELS,
) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(sample_rate)
        writer.writeframes(audio)
    return output.getvalue()


def _audio_for_cloud_stt(request: Request, audio: bytes) -> tuple[bytes, str]:
    """Return a valid upload payload for cloud STT APIs.

    Home Assistant's live Assist pipeline streams raw 16-bit PCM chunks while
    advertising WAV/PCM metadata. Cloud HTTP STT APIs expect a real WAV file.
    """

    content_type = request.headers.get("content-type") or "audio/wav"
    metadata = _parse_speech_content(request.headers.get("x-speech-content", ""))
    return _audio_for_cloud_stt_metadata(audio, metadata, content_type)


def _audio_for_cloud_stt_metadata(
    audio: bytes,
    metadata: dict[str, Any],
    content_type: str = "audio/wav",
) -> tuple[bytes, str]:
    """Return a valid upload payload using parsed Home Assistant speech metadata."""

    audio_format = metadata.get("format", "wav").lower()
    codec = metadata.get("codec", "pcm").lower()
    if audio_format == "wav" and codec == "pcm" and not audio.startswith(b"RIFF"):
        sample_rate = int(metadata.get("sample_rate") or DEFAULT_HA_STT_SAMPLE_RATE)
        bit_rate = int(metadata.get("bit_rate") or 16)
        channels = int(metadata.get("channel") or DEFAULT_HA_STT_CHANNELS)
        sample_width = max(1, bit_rate // 8)
        return _wav_from_pcm(
            audio,
            sample_rate=sample_rate,
            sample_width=sample_width,
            channels=channels,
        ), "audio/wav"
    return audio, content_type


def _pcm_stream_payload(chunk: bytes) -> bytes:
    """Return raw PCM payload, stripping a WAV header when a streamed chunk has one."""

    if chunk.startswith(b"RIFF"):
        data_index = chunk.find(b"data")
        if data_index >= 0 and len(chunk) >= data_index + 8:
            return chunk[data_index + 8 :]
    return chunk


def _ws_metadata_value(metadata: dict[str, Any], key: str, default: Any) -> Any:
    value = metadata.get(key)
    return default if value in {None, ""} else value


def _stt_metadata_from_start(start: dict[str, Any]) -> dict[str, Any]:
    metadata = start.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _preferred_tts_format(payload: dict[str, Any]) -> str:
    options = payload.get("options")
    if not isinstance(options, dict):
        return "mp3"
    preferred = str(options.get("preferred_format") or "").strip().lower()
    return preferred if preferred in AUDIO_TTS_FORMATS else "mp3"


def _gemini_model_path(model: str) -> str:
    clean = (model or "").strip() or DEFAULT_GEMINI_TEXT_MODEL
    return clean.removeprefix("models/")


async def _gemini_generate_content(
    api_key: str,
    model: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{quote(_gemini_model_path(model), safe='')}:generateContent"

    async def request() -> httpx.Response:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(url, params={"key": api_key}, json=payload)
            response.raise_for_status()
            return response

    response = await _provider_call("Gemini", request)
    data = response.json()
    return data if isinstance(data, dict) else {}


def _gemini_text(data: dict[str, Any]) -> str:
    for candidate in data.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            text = part.get("text")
            if text:
                return str(text).strip()
    return ""


def _gemini_inline_audio(data: dict[str, Any]) -> tuple[bytes, str]:
    for candidate in data.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data") or {}
            encoded = inline.get("data")
            if encoded:
                return base64.b64decode(encoded), str(inline.get("mimeType") or inline.get("mime_type") or "audio/wav")
    raise HTTPException(status_code=502, detail="Gemini did not return audio")


def _gemini_tts_model_candidates(model: str, integration: IntegrationConfig) -> list[str]:
    fallback_models = {item.removeprefix("models/") for item in GEMINI_TTS_FALLBACK_MODELS}
    custom_candidates = []
    for candidate in (model, integration.default_tts_model):
        clean = (candidate or "").strip()
        if clean and clean.removeprefix("models/") not in fallback_models:
            custom_candidates.append(clean)
    candidates = [*custom_candidates, DEFAULT_GEMINI_TTS_MODEL, *GEMINI_TTS_FALLBACK_MODELS]
    seen: set[str] = set()
    values: list[str] = []
    for candidate in candidates:
        clean = (candidate or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        values.append(clean)
    return values


def _gemini_no_audio_reason(data: dict[str, Any]) -> str:
    reasons: list[str] = []
    for candidate in data.get("candidates") or []:
        reason = str(candidate.get("finishReason") or candidate.get("finish_reason") or "").strip()
        if reason:
            reasons.append(reason)
    prompt_feedback = data.get("promptFeedback") or data.get("prompt_feedback") or {}
    block_reason = str(prompt_feedback.get("blockReason") or prompt_feedback.get("block_reason") or "").strip()
    if block_reason:
        reasons.append(f"prompt blocked: {block_reason}")
    return ", ".join(dict.fromkeys(reasons))


def _audio_extension_for_media_type(media_type: str) -> str:
    clean = media_type.lower().split(";")[0].strip()
    return {
        "audio/aac": "aac",
        "audio/flac": "flac",
        "audio/l16": "wav",
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/ogg": "opus",
        "audio/pcm": "wav",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
    }.get(clean, "wav")


def _mime_param(media_type: str, name: str) -> str:
    for chunk in media_type.split(";")[1:]:
        key, _, value = chunk.strip().partition("=")
        if key.lower() == name:
            return value.strip()
    return ""


def _normalize_inline_audio(audio: bytes, media_type: str) -> tuple[bytes, str, str]:
    extension = _audio_extension_for_media_type(media_type)
    if extension == "wav" and not audio.startswith(b"RIFF"):
        sample_rate = int(_mime_param(media_type, "rate") or _mime_param(media_type, "sample_rate") or 24000)
        return (
            _wav_from_pcm(audio, sample_rate=sample_rate, sample_width=2, channels=1),
            "audio/wav",
            "wav",
        )
    return audio, media_type.split(";")[0] or "audio/wav", extension


def _ha_assist_step_integration(
    config: RuntimeConfig,
    flow: FlowConfig,
    kind: str,
    supported_kinds: set[str],
) -> tuple[Any, IntegrationConfig | None]:
    """Return the explicit HA Assist step integration without provider fallback."""

    step, integration = _step_integration(config, flow, kind)
    if not step:
        return None, None
    if not integration:
        return step, None
    if integration.kind not in supported_kinds:
        raise HTTPException(
            status_code=400,
            detail=(
                f"HA Assist {kind.upper()} bridge cannot use {integration.name} "
                f"({integration.kind}). Select a {kind.upper()} integration supported by HA Assist."
            ),
        )
    return step, integration


def _bridge_unavailable(role: str, supported_names: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail=(
            f"HA Assist {role} requires an enabled explicit {role} step. "
            f"Configure one of: {supported_names}. Gemini Live speech-to-speech "
            "pipelines are bridged automatically when Home Assistant sends mono 16-bit PCM audio."
        ),
    )


def _ha_supported_stt_names() -> str:
    return "Google Gemini Cloud and Google Cloud STT"


def _ha_supported_tts_names() -> str:
    return "Google Gemini Cloud, Google Cloud TTS, and Google Cloud TTS Streaming"


def _streaming_stt_kind(integration: IntegrationConfig | None, model: str) -> str:
    return ""


def _ha_stt_model_fallback(integration: IntegrationConfig | None) -> str:
    if not integration:
        return ""
    if integration.kind in {"gemini", "gemini_cloud"}:
        return DEFAULT_GEMINI_TEXT_MODEL
    return integration.default_stt_model or integration.default_model or ""


def _ha_tts_model_fallback(integration: IntegrationConfig | None) -> str:
    if not integration:
        return ""
    if integration.kind in {"gemini", "gemini_cloud"}:
        return DEFAULT_GEMINI_TTS_MODEL
    if integration.kind == "google_cloud_tts":
        return "google-tts"
    if integration.kind == "google_streaming_tts":
        return "google-streaming-tts"
    return integration.default_tts_model or integration.default_model or ""


def _ha_tts_voice_fallback(integration: IntegrationConfig | None) -> str:
    if not integration:
        return ""
    if integration.kind in {"gemini", "gemini_cloud"}:
        return DEFAULT_GEMINI_LIVE_VOICE
    if integration.kind in {"google_cloud_tts", "google_streaming_tts"}:
        return DEFAULT_GOOGLE_TTS_VOICE
    return integration.default_voice or ""


def _normalize_language_hint(language: str | None) -> str:
    value = (language or "").strip()
    if not value or value.lower() == "pipecat-assist":
        return ""
    return value.split("-")[0].lower()


def _websocket_connect(url: str, headers: dict[str, str]):
    import inspect
    import websockets

    header_key = (
        "additional_headers"
        if "additional_headers" in inspect.signature(websockets.connect).parameters
        else "extra_headers"
    )
    return websockets.connect(url, **{header_key: headers})


async def _send_ws_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    with suppress(Exception):
        await websocket.send_json(payload)


async def _transcribe_audio_bytes(
    *,
    config: RuntimeConfig,
    flow: FlowConfig,
    audio: bytes,
    metadata: dict[str, Any],
    content_type: str,
) -> dict[str, str]:
    step, integration = _ha_assist_step_integration(config, flow, "stt", HA_STT_BRIDGE_KINDS)
    if not integration:
        raise _bridge_unavailable("STT", _ha_supported_stt_names())
    integration = _require_integration(integration, "STT", fields=())
    model = _step_model_for(step, integration, "stt", _ha_stt_model_fallback(integration))
    if not audio:
        raise HTTPException(status_code=400, detail="No audio was provided")
    stt_audio, stt_content_type = _audio_for_cloud_stt_metadata(audio, metadata, content_type)

    if integration.kind in {"gemini", "gemini_cloud"}:
        api_key = _integration_api_key_or_400(integration, "STT", os.getenv("GOOGLE_API_KEY", ""))
        data = await _gemini_generate_content(
            api_key,
            integration.default_model or flow.text_model or DEFAULT_GEMINI_TEXT_MODEL,
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    "Transcribe the provided speech to plain text. "
                                    "Return only the transcript, without commentary."
                                )
                            },
                            {
                                "inlineData": {
                                    "mimeType": stt_content_type,
                                    "data": base64.b64encode(stt_audio).decode("ascii"),
                                }
                            },
                        ],
                    }
                ]
            },
        )
        return {"text": _gemini_text(data)}

    raise HTTPException(
        status_code=400,
        detail=f"HA Assist STT bridge does not support {integration.name}. Use Google Gemini.",
    )


async def _streaming_transcribe_audio(
    *,
    websocket: WebSocket,
    queue: "asyncio.Queue[bytes | None]",
    config: RuntimeConfig,
    flow: FlowConfig,
    step,
    integration: IntegrationConfig,
    model: str,
    metadata: dict[str, Any],
) -> str:
    raise RuntimeError(f"{integration.name} does not expose a HA Assist streaming STT bridge; use buffered mode")


def _tts_prefetch_key(flow_id: str, text: str, response_format: str) -> tuple[str, str, str]:
    return (flow_id, response_format, text.strip())


def _prune_tts_prefetch() -> None:
    now = time.time()
    stale = [
        key
        for key, (created_at, task) in TTS_PREFETCH.items()
        if now - created_at > TTS_PREFETCH_TTL_SECONDS or task.cancelled()
    ]
    for key in stale:
        TTS_PREFETCH.pop(key, None)


def _pop_tts_prefetch(
    flow_id: str,
    text: str,
    response_format: str,
) -> tuple[str, float, Any] | None:
    exact_key = _tts_prefetch_key(flow_id, text, response_format)
    cached = TTS_PREFETCH.pop(exact_key, None)
    if cached:
        return "exact", cached[0], cached[1]

    clean = text.strip()
    for key in list(TTS_PREFETCH):
        cached_flow_id, _, cached_text = key
        if cached_flow_id == flow_id and cached_text == clean:
            created_at, task = TTS_PREFETCH.pop(key)
            return "format-fallback", created_at, task
    return None


def _google_tts_language_code(voice: str, language: str) -> str:
    parts = [part for part in (voice or "").split("-") if part]
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    value = (language or "").replace("_", "-")
    if value and value.lower() != "pipecat-assist":
        language_parts = value.split("-")
        if len(language_parts) >= 2:
            return f"{language_parts[0]}-{language_parts[1].upper()}"
        return f"{language_parts[0]}-{language_parts[0].upper()}"
    return "en-US"


async def _google_cloud_tts_audio(
    *,
    integration: IntegrationConfig,
    text: str,
    voice: str,
    language: str,
    speed: float,
) -> tuple[bytes, str, str]:
    from google.api_core.client_options import ClientOptions
    from google.cloud import texttospeech_v1

    credentials = _google_cloud_credentials(integration)
    client_options = None
    if integration.location:
        client_options = ClientOptions(api_endpoint=f"{integration.location}-texttospeech.googleapis.com")
    client = texttospeech_v1.TextToSpeechAsyncClient(
        credentials=credentials,
        client_options=client_options,
    )
    sample_rate = 24000
    voice_name = voice or DEFAULT_GOOGLE_TTS_VOICE
    request = texttospeech_v1.SynthesizeSpeechRequest(
        input=texttospeech_v1.SynthesisInput(text=text),
        voice=texttospeech_v1.VoiceSelectionParams(
            language_code=_google_tts_language_code(voice_name, language),
            name=voice_name,
        ),
        audio_config=texttospeech_v1.AudioConfig(
            audio_encoding=texttospeech_v1.AudioEncoding.LINEAR16,
            sample_rate_hertz=sample_rate,
            speaking_rate=speed,
        ),
    )
    response = await client.synthesize_speech(request=request)
    audio = response.audio_content
    if audio.startswith(b"RIFF"):
        return audio, "audio/wav", "wav"
    return _wav_from_pcm(audio, sample_rate=sample_rate, sample_width=2, channels=1), "audio/wav", "wav"


async def _synthesize_gemini_tts_audio(
    *,
    api_key: str,
    integration: IntegrationConfig,
    model: str,
    text: str,
    voice: str,
) -> tuple[bytes, str, str]:
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice or integration.default_voice or DEFAULT_GEMINI_LIVE_VOICE,
                    }
                }
            },
        },
    }
    last_error: HTTPException | None = None
    for candidate in _gemini_tts_model_candidates(model, integration):
        for attempt in range(2):
            data: dict[str, Any] | None = None
            try:
                data = await _gemini_generate_content(api_key, candidate, payload)
                return _normalize_inline_audio(*_gemini_inline_audio(data))
            except HTTPException as err:
                if err.status_code in {401, 403}:
                    raise
                reason = ""
                if err.status_code == 502 and data is not None:
                    reason = _gemini_no_audio_reason(data)
                detail = f"{err.detail}{f' ({reason})' if reason else ''}"
                last_error = HTTPException(status_code=err.status_code, detail=detail)
                logger.debug(
                    "Gemini TTS model {} attempt {}/2 failed: {}",
                    candidate,
                    attempt + 1,
                    detail,
                )
                if err.status_code == 502 and data is not None and attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                break
    if last_error:
        raise last_error
    raise HTTPException(status_code=502, detail="Gemini TTS could not select a model")


async def _synthesize_tts_audio(
    *,
    config: RuntimeConfig,
    flow: FlowConfig,
    text: str,
    payload: dict[str, Any],
) -> tuple[bytes, str, str]:
    started_at = time.perf_counter()
    step, integration = _ha_assist_step_integration(config, flow, "tts", HA_TTS_BRIDGE_KINDS)
    if not integration:
        raise _bridge_unavailable("TTS", _ha_supported_tts_names())
    integration = _require_integration(integration, "TTS", fields=())
    model = _step_model_for(step, integration, "tts", _ha_tts_model_fallback(integration))
    voice = _step_voice(step, integration, _ha_tts_voice_fallback(integration))
    response_format = _preferred_tts_format(payload)
    language = _runtime_language(flow, integration, payload.get("language"))
    speed = _runtime_speed(flow, integration)
    logger.info(
        "HA Assist TTS synth started flow={} integration={} kind={} model={} voice={} text={} requested_format={}",
        flow.id,
        integration.name,
        integration.kind,
        model,
        voice,
        _text_fingerprint(text),
        response_format,
    )

    if integration.kind in {"gemini", "gemini_cloud"}:
        api_key = _integration_api_key_or_400(integration, "TTS", os.getenv("GOOGLE_API_KEY", ""))
        audio = await _synthesize_gemini_tts_audio(
            api_key=api_key,
            integration=integration,
            model=model,
            text=text,
            voice=voice,
        )
        logger.info(
            "HA Assist TTS synth finished flow={} integration={} model={} voice={} duration_ms={:.0f}",
            flow.id,
            integration.name,
            model or DEFAULT_GEMINI_TTS_MODEL,
            voice or DEFAULT_GEMINI_LIVE_VOICE,
            (time.perf_counter() - started_at) * 1000,
        )
        return audio

    if integration.kind in {"google_cloud_tts", "google_streaming_tts"}:
        audio = await _provider_call(
            f"{integration.name} TTS",
            lambda: _google_cloud_tts_audio(
                integration=integration,
                text=text,
                voice=voice,
                language=language,
                speed=speed,
            ),
        )
        logger.info(
            "HA Assist TTS synth finished flow={} integration={} model={} voice={} duration_ms={:.0f}",
            flow.id,
            integration.name,
            model,
            voice or DEFAULT_GOOGLE_TTS_VOICE,
            (time.perf_counter() - started_at) * 1000,
        )
        return audio

    raise HTTPException(
        status_code=400,
        detail=f"HA Assist TTS bridge does not support {integration.name}. Use Google Gemini or Google Cloud TTS.",
    )


def _start_tts_prefetch(
    *,
    config: RuntimeConfig,
    flow: FlowConfig,
    text: str,
    language: str | None,
) -> None:
    from app.services.ha_live_bridge import _find_ha_live_turn_by_speech

    text = text.strip()
    if not text:
        return
    if _find_ha_live_turn_by_speech(flow.id, text):
        return
    _prune_tts_prefetch()
    payload = {
        "text": text,
        "language": language or flow.language or "en",
        "options": {"preferred_format": "mp3"},
        "flow_id": flow.id,
    }
    key = _tts_prefetch_key(flow.id, text, "mp3")
    if key in TTS_PREFETCH:
        return

    async def runner() -> tuple[bytes, str, str]:
        started_at = time.perf_counter()
        try:
            return await _synthesize_tts_audio(config=config, flow=flow, text=text, payload=payload)
        finally:
            logger.info(
                "HA Assist TTS prefetch finished flow={} text={} duration_ms={:.0f}",
                flow.id,
                _text_fingerprint(text),
                (time.perf_counter() - started_at) * 1000,
            )

    task = asyncio.create_task(runner())
    logger.info(
        "HA Assist TTS prefetch started flow={} text={} format={}",
        flow.id,
        _text_fingerprint(text),
        "mp3",
    )
    task.add_done_callback(lambda item: item.exception() if not item.cancelled() else None)
    TTS_PREFETCH[key] = (time.time(), task)

