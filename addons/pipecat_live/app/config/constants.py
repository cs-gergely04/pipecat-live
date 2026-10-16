"""Constants and environment helpers for Pipecat Assist runtime configuration."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.getenv("PIPECAT_LIVE_DATA_DIR", os.getenv("PIPECAT_ASSIST_DATA_DIR", "/data")))
CONFIG_PATH = DATA_DIR / "pipecat_live.json"
REDACTED = "__redacted__"

DEFAULT_INSTRUCTIONS = (
    "You are a realtime Home Assistant voice agent. Speak naturally and briefly. "
    "Use Home Assistant MCP tools only when the user clearly asks to control, "
    "inspect, or automate the home. Never invent device state. If a room, "
    "device, or action is ambiguous, ask one short clarification."
)

DEFAULT_GEMINI_TEXT_MODEL = "gemini-3.8-flash"                # Replaces deprecated 2.5-flash
DEFAULT_GEMINI_LIVE_MODEL = "gemini-3.8-live"                 # Replaces 3.1-preview-live
DEFAULT_GEMINI_LIVE_VOICE = "Charon"                          # Valid Live Audio Voice

DEFAULT_GEMINI_TTS_MODEL = "gemini-3.8-flash-tts"             # Replaces 3.1-preview-tts
LEGACY_GEMINI_TTS_MODELS = {
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
}
GEMINI_TTS_FALLBACK_MODELS = (
    DEFAULT_GEMINI_TTS_MODEL,
    "gemini-3.8-flash-lite-tts",                              # Stable cost-efficient backup
)

DEFAULT_GOOGLE_TTS_VOICE = "en-US-Chirp3-HD-Charon"
DEFAULT_WEB_SEARCH_MODEL = DEFAULT_GEMINI_TEXT_MODEL
DEFAULT_GOOGLE_IMAGEN_MODEL = "imagen-4.0-generate-001"
LEGACY_GEMINI_TEXT_MODELS = {
    "gemini-3.5-flash",
    "models/gemini-3.5-flash",
}
GEMINI_LIVE_VOICES = {
    "Aoede",
    "Charon",
    "Fenrir",
    "Kore",
    "Puck",
}
DEFAULT_MCP_URL = "http://supervisor/core/api/mcp"
SECRET_FIELDS = (
    "api_key",
    "token",
    "credentials_json",
)
COMPOSED_STT_PROVIDER_KINDS = {
    "google_cloud_stt",
    "gemini_cloud",
}
COMPOSED_LLM_PROVIDER_KINDS = {
    "gemini_cloud",
}
COMPOSED_TTS_PROVIDER_KINDS = {
    "google_cloud_tts",
    "google_streaming_tts",
    "gemini_cloud",
}
COMPOSED_STEP_PROVIDER_KINDS = {
    "stt": COMPOSED_STT_PROVIDER_KINDS,
    "llm": COMPOSED_LLM_PROVIDER_KINDS,
    "tts": COMPOSED_TTS_PROVIDER_KINDS,
}
ALLOWED_INTEGRATION_KINDS = {
    "gemini",
    "gemini_cloud",
    "google_cloud_stt",
    "google_cloud_tts",
    "google_streaming_tts",
    "google_imagen",
    "web_search",
    "home_assistant_mcp",
    "ha_mcp",
    "mcp_server",
}
ALLOWED_PIPELINE_TEMPLATES = {
    "gemini_live_home",
    "gemini_composed_home",
    "custom",
}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _is_http_url(value: str | None) -> bool:
    value = (value or "").strip().lower()
    return value.startswith("http://") or value.startswith("https://")


def _validate_id(value: str, label: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{label} cannot be empty")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
    if any(char not in allowed for char in clean):
        raise ValueError(f"{label} may only contain letters, numbers, _ and -")
    return clean
