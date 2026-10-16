"""Shared runtime state, constants, and URL/request helpers for Pipecat Assist."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import Request
from loguru import logger

from app.config import ConfigStore, RuntimeConfig
from app.esphome_provisioner import ESPHomeProvisioner

STORE = ConfigStore()
STARTED_AT = time.time()
UI_DIR = Path(__file__).parent / "ui"
UI_CACHE_HEADERS = {"Cache-Control": "no-store"}
SUPERVISOR_URL = os.getenv("SUPERVISOR", "http://supervisor").rstrip("/")

DEFAULT_HA_STT_SAMPLE_RATE = 16000
DEFAULT_HA_STT_SAMPLE_WIDTH = 2
DEFAULT_HA_STT_CHANNELS = 1
AUDIO_TTS_FORMATS = {"mp3", "opus", "aac", "flac", "wav", "pcm"}
TTS_MEDIA_TYPES = {
    "aac": "audio/aac",
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
    "pcm": "audio/L16",
    "wav": "audio/wav",
}
CONVERSATION_END_SYSTEM_HINT = (
    "If the user clearly ends the conversation, briefly acknowledge it and do not ask "
    "a follow-up question. The client will close the microphone after your farewell."
)
HA_STT_BRIDGE_KINDS = {
    "gemini",
    "gemini_cloud",
    "google_cloud_stt",
}
HA_TTS_BRIDGE_KINDS = {
    "gemini",
    "gemini_cloud",
    "google_cloud_tts",
    "google_streaming_tts",
}
IMAGE_GENERATION_KINDS = {"google_imagen"}
PROVIDER_RETRY_STATUSES = {429, 500, 502, 503, 504}
TTS_PREFETCH_TTL_SECONDS = 90
TTS_PREFETCH: dict[tuple[str, str, str], tuple[float, Any]] = {}
HA_LIVE_TURN_TTL_SECONDS = 120
HA_LIVE_TRANSCRIPT_TIMEOUT_SECONDS = 30
HA_LIVE_RESULT_TIMEOUT_SECONDS = 75
HA_LIVE_TURNS_BY_TRANSCRIPT: dict[tuple[str, str], "HALiveTurn"] = {}
HA_LIVE_TURNS_BY_SPEECH: dict[tuple[str, str], "HALiveTurn"] = {}
HA_ASSIST_WARMUP_TASK: asyncio.Task | None = None
ESPHOME_PROVISIONER = ESPHomeProvisioner(
    STORE.load,
    supervisor_url=SUPERVISOR_URL,
    supervisor_token=os.getenv("SUPERVISOR_TOKEN", ""),
)


@dataclass
class HALiveTurn:
    flow_id: str
    provider: str
    started_at: float = field(default_factory=time.time)
    transcript: str = ""
    speech: str = ""
    audio_chunks: list[bytes] = field(default_factory=list)
    audio_sample_rate: int = 24000
    transcript_ready: asyncio.Event = field(default_factory=asyncio.Event)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: str = ""
    task: asyncio.Task | None = None

    @property
    def audio(self) -> bytes:
        return b"".join(self.audio_chunks)


def _configure_logging() -> None:
    config = STORE.load()
    logger.remove()
    logger.add(lambda message: print(message, end=""), level=config.log_level)


def _extract_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("token", "")


def _is_offer_path(path: str) -> bool:
    return path == "/api/offer" or (path.startswith("/sessions/") and path.endswith("/api/offer"))


def _text_fingerprint(text: str) -> str:
    clean = text.strip()
    digest = hashlib.sha1(clean.encode("utf-8")).hexdigest()[:10]
    return f"len={len(clean)} sha1={digest}"


def _offer_url(config: RuntimeConfig, request: Request) -> str:
    host = config.runner_host
    if host in {"0.0.0.0", "::", ""}:
        host = request.url.hostname or "homeassistant.local"
    token = quote(config.satellite_shared_secret)
    suffix = f"?token={token}" if token else ""
    return f"http://{host}:{config.runner_port}/api/offer{suffix}"


def _offer_path(config: RuntimeConfig) -> str:
    token = quote(config.satellite_shared_secret)
    suffix = f"?token={token}" if token else ""
    return f"api/offer{suffix}"


def _esphome_ws_query(config: RuntimeConfig) -> str:
    values = {
        "token": config.satellite_shared_secret,
        "flow_id": config.selected_flow_id,
    }
    return urlencode({key: value for key, value in values.items() if value})


def _esphome_ws_url(config: RuntimeConfig, request: Request) -> str:
    host = config.runner_host
    if host in {"0.0.0.0", "::", ""}:
        host = request.url.hostname or "homeassistant.local"
    query = _esphome_ws_query(config)
    suffix = f"?{query}" if query else ""
    # The host-network runner port is plain WebSocket even when this response
    # was reached through an HTTPS Home Assistant Ingress page.
    return f"ws://{host}:{config.runner_port}/api/assist/esphome{suffix}"


def _esphome_ws_path(config: RuntimeConfig) -> str:
    query = _esphome_ws_query(config)
    suffix = f"?{query}" if query else ""
    return f"api/assist/esphome{suffix}"


def _config_response(config: RuntimeConfig, request: Request) -> dict[str, Any]:
    data = config.public_dict()
    data["runner_offer_url"] = _offer_url(config, request)
    data["runner_offer_path"] = _offer_path(config)
    data["esphome_ws_url"] = _esphome_ws_url(config, request)
    data["esphome_ws_path"] = _esphome_ws_path(config)
    data["esphome_provisioning"] = ESPHOME_PROVISIONER.status()
    return data
