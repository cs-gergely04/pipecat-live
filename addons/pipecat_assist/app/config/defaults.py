"""Default factory functions for Pipecat Assist runtime configuration."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from .constants import (
    DEFAULT_GEMINI_LIVE_MODEL,
    DEFAULT_GEMINI_LIVE_VOICE,
    DEFAULT_GEMINI_TEXT_MODEL,
    DEFAULT_GEMINI_TTS_MODEL,
    DEFAULT_GOOGLE_IMAGEN_MODEL,
    DEFAULT_GOOGLE_TTS_VOICE,
    DEFAULT_INSTRUCTIONS,
    _env_bool,
    _env_int,
    _split_csv,
)
from .models import IntegrationConfig, PipelineStepConfig

if TYPE_CHECKING:
    from .models import RuntimeConfig


def default_integrations() -> list[IntegrationConfig]:
    """Return first-run integrations shown in the UI."""

    return [
        IntegrationConfig(
            id="gemini",
            name="Google Gemini Live",
            kind="gemini",
            enabled=bool(os.getenv("GOOGLE_API_KEY")),
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            default_realtime_model=os.getenv("GEMINI_LIVE_MODEL", DEFAULT_GEMINI_LIVE_MODEL),
            default_tts_model=os.getenv("GEMINI_TTS_MODEL", DEFAULT_GEMINI_TTS_MODEL),
            default_voice=os.getenv("GEMINI_LIVE_VOICE", DEFAULT_GEMINI_LIVE_VOICE),
        ),
        IntegrationConfig(
            id="gemini-cloud",
            name="Google Gemini Cloud",
            kind="gemini_cloud",
            enabled=bool(os.getenv("GOOGLE_API_KEY")),
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            default_model=os.getenv("GEMINI_TEXT_MODEL", DEFAULT_GEMINI_TEXT_MODEL),
            default_tts_model=os.getenv("GEMINI_TTS_MODEL", DEFAULT_GEMINI_TTS_MODEL),
        ),
        IntegrationConfig(
            id="google-cloud-stt",
            name="Google Cloud STT",
            kind="google_cloud_stt",
            enabled=bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS")),
            credentials_path=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
            location=os.getenv("GOOGLE_CLOUD_STT_LOCATION", ""),
            default_stt_model="latest_short",
        ),
        IntegrationConfig(
            id="google-cloud-tts",
            name="Google Cloud TTS HTTP",
            kind="google_cloud_tts",
            enabled=bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS")),
            credentials_path=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
            location=os.getenv("GOOGLE_CLOUD_TTS_LOCATION", ""),
            default_model="google-tts",
            default_voice=os.getenv("GOOGLE_TTS_VOICE", DEFAULT_GOOGLE_TTS_VOICE),
        ),
        IntegrationConfig(
            id="google-streaming-tts",
            name="Google Cloud TTS Streaming",
            kind="google_streaming_tts",
            enabled=bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS")),
            credentials_path=os.getenv("GOOGLE_APPLICATION_CREDENTIALS", ""),
            location=os.getenv("GOOGLE_CLOUD_TTS_LOCATION", ""),
            default_model="google-streaming-tts",
            default_voice=os.getenv("GOOGLE_TTS_VOICE", DEFAULT_GOOGLE_TTS_VOICE),
            tts_streaming_mode="sentence",
        ),
        IntegrationConfig(
            id="web-search",
            name="Web Search",
            kind="web_search",
            enabled=False,
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            provider_id="gemini-cloud",
            default_model=os.getenv("GEMINI_TEXT_MODEL", DEFAULT_GEMINI_TEXT_MODEL),
        ),
        IntegrationConfig(
            id="google-imagen",
            name="Google Imagen",
            kind="google_imagen",
            enabled=bool(os.getenv("GOOGLE_API_KEY")),
            api_key=os.getenv("GOOGLE_API_KEY", ""),
            default_model=os.getenv("GOOGLE_IMAGEN_MODEL", DEFAULT_GOOGLE_IMAGEN_MODEL),
        ),
        IntegrationConfig(
            id="ha-mcp",
            name="Home Assistant MCP",
            kind="home_assistant_mcp",
            enabled=True,
            base_url=os.getenv("HA_MCP_URL", ""),
            token=os.getenv("LONGLIVED_TOKEN", ""),
        ),
        IntegrationConfig(
            id="ha-mcp-server",
            name="HA MCP Server Add-on",
            kind="ha_mcp",
            enabled=False,
            base_url=os.getenv("HA_MCP_SERVER_URL", ""),
        ),
    ]


def default_steps() -> list[PipelineStepConfig]:
    """Return the default visible Pipecat pipeline."""

    return [
        PipelineStepConfig(id="transport", kind="transport", label="SmallWebRTC"),
        PipelineStepConfig(id="memory", kind="memory", label="Session memory"),
        PipelineStepConfig(
            id="vad",
            kind="vad",
            label="Gemini VAD",
            settings={"mode": "semantic_vad", "eagerness": "low"},
        ),
        PipelineStepConfig(
            id="llm",
            kind="llm",
            label="Live model",
            integration_id="gemini",
            model=os.getenv("GEMINI_LIVE_MODEL", DEFAULT_GEMINI_LIVE_MODEL),
        ),
        PipelineStepConfig(
            id="web-search",
            kind="web_search",
            label="Web Search",
            enabled=False,
            integration_id="web-search",
            settings={"announce": True},
        ),
        PipelineStepConfig(
            id="tools",
            kind="tools",
            label="HA MCP tools",
            integration_id="ha-mcp",
        ),
        PipelineStepConfig(
            id="output",
            kind="output",
            label="Native audio",
            integration_id="gemini",
            voice=os.getenv("GEMINI_LIVE_VOICE", DEFAULT_GEMINI_LIVE_VOICE),
        ),
    ]


def default_conversation_flow() -> dict[str, Any]:
    """Return a transparent Pipecat Flows graph for composed realtime pipelines."""

    return {
        "enabled": False,
        "initial_node_id": "passthrough",
        "nodes": [
            {
                "id": "passthrough",
                "label": "Pass-through",
                "role_message": DEFAULT_INSTRUCTIONS,
                "task": "Continue the conversation normally without changing the pipeline behavior.",
                "functions": [],
                "respond_immediately": False,
            },
        ],
    }


def default_config_from_environment() -> RuntimeConfig:
    """Create the first-run configuration from add-on options or environment."""

    from .models import FlowConfig, RuntimeConfig

    instructions = os.getenv("INSTRUCTIONS") or DEFAULT_INSTRUCTIONS
    tool_allowlist = _split_csv(os.getenv("MCP_TOOL_ALLOWLIST"))
    flow = FlowConfig(
        model=os.getenv("GEMINI_LIVE_MODEL", DEFAULT_GEMINI_LIVE_MODEL),
        text_model=os.getenv("GEMINI_TEXT_MODEL", DEFAULT_GEMINI_TEXT_MODEL),
        voice=os.getenv("GEMINI_LIVE_VOICE", DEFAULT_GEMINI_LIVE_VOICE),
        instructions=instructions,
        mcp_tool_allowlist=tool_allowlist,
    )
    return RuntimeConfig(
        google_api_key=os.getenv("GOOGLE_API_KEY", ""),
        text_model=os.getenv("GEMINI_TEXT_MODEL", DEFAULT_GEMINI_TEXT_MODEL),
        ha_mcp_url=os.getenv("HA_MCP_URL", ""),
        longlived_token=os.getenv("LONGLIVED_TOKEN", ""),
        satellite_shared_secret=os.getenv("SATELLITE_SHARED_SECRET", ""),
        runner_host=os.getenv("RUNNER_HOST", ""),
        runner_port=_env_int("RUNNER_PORT", 7860),
        esphome_follow_up_ms=max(
            0, min(60000, _env_int("ESPHOME_FOLLOW_UP_MS", 30000))
        ),
        esphome_follow_up_open_delay_ms=max(
            0, min(5000, _env_int("ESPHOME_FOLLOW_UP_OPEN_DELAY_MS", 80))
        ),
        esphome_wake_open_delay_ms=max(
            0, min(5000, _env_int("ESPHOME_WAKE_OPEN_DELAY_MS", 0))
        ),
        esphome_playback_prebuffer_ms=max(
            0, min(2000, _env_int("ESPHOME_PLAYBACK_PREBUFFER_MS", 300))
        ),
        esp32_mode=_env_bool("ESP32_MODE", False),
        audio_debug_enabled=_env_bool("AUDIO_DEBUG_ENABLED", False),
        audio_debug_keep_sessions=min(100, max(1, _env_int("AUDIO_DEBUG_KEEP_SESSIONS", 10))),
        session_memory_enabled=_env_bool("SESSION_MEMORY_ENABLED", True),
        session_memory_reuse_seconds=max(0, min(86400, _env_int("SESSION_MEMORY_REUSE_SECONDS", 300))),
        session_memory_max_messages=max(0, min(100, _env_int("SESSION_MEMORY_MAX_MESSAGES", 12))),
        mcp_tools_cache_enabled=_env_bool("MCP_TOOLS_CACHE_ENABLED", True),
        mcp_tools_cache_ttl_seconds=max(0, min(86400, _env_int("MCP_TOOLS_CACHE_TTL_SECONDS", 300))),
        ai_image_provider_id=os.getenv("AI_IMAGE_PROVIDER_ID", ""),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        flows=[flow],
    )
