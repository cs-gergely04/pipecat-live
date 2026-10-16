"""Configuration migration, sanitization, and provider default repair helpers."""

from __future__ import annotations

import os
from typing import Any

from .constants import (
    ALLOWED_INTEGRATION_KINDS,
    ALLOWED_PIPELINE_TEMPLATES,
    DEFAULT_GEMINI_LIVE_MODEL,
    DEFAULT_GEMINI_LIVE_VOICE,
    DEFAULT_GEMINI_TEXT_MODEL,
    DEFAULT_GEMINI_TTS_MODEL,
    DEFAULT_GOOGLE_IMAGEN_MODEL,
    DEFAULT_GOOGLE_TTS_VOICE,
    GEMINI_LIVE_VOICES,
    LEGACY_GEMINI_TEXT_MODELS,
    LEGACY_GEMINI_TTS_MODELS,
    _is_http_url,
)
from .models import FlowConfig, PipelineStepConfig, RuntimeConfig


def _is_realtime_model_for_provider(provider_kind: str, model: str) -> bool:
    """Return whether a realtime model value belongs to the selected provider."""

    model = (model or "").strip()
    if not model:
        return False
    if provider_kind == "gemini":
        return "gemini" in model.lower()
    return False


def _is_voice_for_provider(provider_kind: str, voice: str) -> bool:
    """Return whether a realtime voice value belongs to the selected provider."""

    voice = (voice or "").strip()
    if not voice:
        return False
    return voice in GEMINI_LIVE_VOICES


def _flow_output_step(flow: FlowConfig) -> PipelineStepConfig | None:
    return next(
        (step for step in flow.steps if step.kind in {"output", "tts"} and step.enabled),
        None,
    )


def _repair_flow_provider_model(config: RuntimeConfig, flow: FlowConfig) -> bool:
    """Ensure realtime flow uses valid Gemini Live model and voice."""

    if flow.mode in {"composed", "classic"}:
        return False

    model_step = flow.model_step()
    changed = False

    if flow.provider_id != "gemini":
        flow.provider_id = "gemini"
        changed = True

    if model_step and model_step.integration_id != "gemini":
        model_step.integration_id = "gemini"
        changed = True

    default_model = DEFAULT_GEMINI_LIVE_MODEL
    default_voice = DEFAULT_GEMINI_LIVE_VOICE
    output_step = _flow_output_step(flow)

    if model_step and model_step.model and not _is_realtime_model_for_provider("gemini", model_step.model):
        model_step.model = ""
        changed = True

    if not _is_realtime_model_for_provider("gemini", flow.model):
        flow.model = default_model
        changed = True

    if output_step:
        if output_step.integration_id != "gemini":
            output_step.integration_id = "gemini"
            changed = True
        if output_step.voice and not _is_voice_for_provider("gemini", output_step.voice):
            output_step.voice = ""
            changed = True

    if not _is_voice_for_provider("gemini", flow.voice):
        flow.voice = default_voice
        changed = True

    return changed


def _is_composed_flow(flow: FlowConfig) -> bool:
    has_stt = any(step.kind == "stt" and step.enabled for step in flow.steps)
    has_tts = any(step.kind == "tts" and step.enabled for step in flow.steps)
    return flow.mode in {"composed", "classic"} or has_stt or has_tts


def _clean_model_name(model: str | None) -> str:
    return (model or "").strip()


def _is_legacy_gemini_text_model(model: str | None) -> bool:
    return _clean_model_name(model) in LEGACY_GEMINI_TEXT_MODELS


def _is_legacy_gemini_tts_model(model: str | None) -> bool:
    return _clean_model_name(model) in LEGACY_GEMINI_TTS_MODELS


def _looks_like_provider_mismatch(provider_kind: str, model: str | None) -> bool:
    clean = _clean_model_name(model).removeprefix("models/")
    if not clean:
        return False
    if provider_kind in {"gemini", "gemini_cloud"}:
        return not clean.startswith("gemini-")
    return False


def _repair_text_model_defaults(config: RuntimeConfig) -> bool:
    """Replace stale text model defaults that are invalid for current providers."""

    changed = False
    gemini_default = os.getenv("GEMINI_TEXT_MODEL", DEFAULT_GEMINI_TEXT_MODEL)

    if _is_legacy_gemini_text_model(config.text_model) or _looks_like_provider_mismatch("gemini_cloud", config.text_model):
        config.text_model = gemini_default
        changed = True

    for integration in config.integrations:
        if integration.kind == "gemini_cloud" and (
            not integration.default_model or _is_legacy_gemini_text_model(integration.default_model) or _looks_like_provider_mismatch("gemini_cloud", integration.default_model)
        ):
            integration.default_model = gemini_default
            changed = True

    for flow in config.flows:
        if _is_legacy_gemini_text_model(flow.text_model) or _looks_like_provider_mismatch("gemini_cloud", flow.text_model):
            flow.text_model = gemini_default
            changed = True
        for step in flow.steps:
            if step.kind != "llm" or not step.model:
                continue
            integration = config.integration(step.integration_id)
            provider_kind = integration.kind if integration else step.integration_id
            if _is_legacy_gemini_text_model(step.model) or _looks_like_provider_mismatch(provider_kind, step.model):
                step.model = gemini_default
                changed = True

    return changed


def _repair_composed_gemini_integrations(config: RuntimeConfig, flow: FlowConfig) -> bool:
    """Move composed Gemini steps away from the speech-to-speech live integration."""

    if not _is_composed_flow(flow):
        return False

    changed = False
    has_gemini_cloud = bool(config.integration("gemini-cloud"))

    for step in flow.steps:
        if step.integration_id != "gemini":
            continue
        if step.kind == "llm" and has_gemini_cloud:
            step.integration_id = "gemini-cloud"
        elif step.kind == "stt":
            step.integration_id = "google-cloud-stt" if config.integration("google-cloud-stt") else "gemini-cloud"
        elif step.kind == "tts":
            step.integration_id = "google-streaming-tts" if config.integration("google-streaming-tts") else "gemini-cloud"
        else:
            continue
        step.model = ""
        step.voice = ""
        changed = True

    if flow.provider_id == "gemini" and has_gemini_cloud:
        flow.provider_id = "gemini-cloud"
        changed = True

    return changed


def _strip_unsupported_s2s_flow_steps(config: RuntimeConfig, flow: FlowConfig) -> bool:
    """Remove Pipecat Flow steps from speech-to-speech pipelines."""

    model_step = flow.model_step()
    integration_id = (
        model_step.integration_id if model_step and model_step.integration_id else flow.provider_id
    )
    integration = config.integration(integration_id)
    provider_kind = integration.kind if integration else flow.provider_id
    has_stt = any(step.kind == "stt" and step.enabled for step in flow.steps)
    has_tts = any(step.kind == "tts" and step.enabled for step in flow.steps)
    is_s2s = flow.mode == "realtime" or (
        not has_stt and not has_tts and provider_kind == "gemini"
    )
    if not is_s2s:
        return False

    original_count = len(flow.steps)
    flow.steps = [step for step in flow.steps if step.kind != "flow"]
    changed = len(flow.steps) != original_count
    if flow.conversation_flow.get("enabled"):
        flow.conversation_flow["enabled"] = False
        changed = True
    return changed


def _repair_mcp_url_overrides(config: RuntimeConfig) -> bool:
    """Clear custom MCP URLs that httpx cannot use."""

    changed = False
    if config.ha_mcp_url and not _is_http_url(config.ha_mcp_url):
        config.ha_mcp_url = ""
        changed = True

    mcp = config.mcp_integration
    if mcp and mcp.base_url and not _is_http_url(mcp.base_url):
        mcp.base_url = ""
        changed = True

    return changed


def _repair_provider_defaults(config: RuntimeConfig) -> bool:
    """Repair provider defaults that can be pasted across integrations."""

    changed = False

    gemini = config.integration("gemini")
    if gemini:
        if gemini.name in {"Google Gemini", "Gemini"}:
            gemini.name = "Google Gemini Live"
            changed = True
        if gemini.default_model:
            gemini.default_model = ""
            changed = True
        if gemini.default_realtime_model in {"", "gemini-2.5-flash-live"}:
            gemini.default_realtime_model = os.getenv(
                "GEMINI_LIVE_MODEL",
                DEFAULT_GEMINI_LIVE_MODEL,
            )
            changed = True
        if gemini.default_realtime_model.startswith("models/"):
            gemini.default_realtime_model = gemini.default_realtime_model.removeprefix("models/")
            changed = True
        if not _is_voice_for_provider("gemini", gemini.default_voice):
            gemini.default_voice = os.getenv("GEMINI_LIVE_VOICE", DEFAULT_GEMINI_LIVE_VOICE)
            changed = True
        if not gemini.default_tts_model or _is_legacy_gemini_tts_model(gemini.default_tts_model):
            gemini.default_tts_model = os.getenv("GEMINI_TTS_MODEL", DEFAULT_GEMINI_TTS_MODEL)
            changed = True

    gemini_cloud = config.integration("gemini-cloud")
    if gemini_cloud:
        if gemini_cloud.name != "Google Gemini Cloud":
            gemini_cloud.name = "Google Gemini Cloud"
            changed = True
        if gemini and gemini.api_key and not gemini_cloud.api_key:
            gemini_cloud.api_key = gemini.api_key
            gemini_cloud.enabled = gemini.enabled
            changed = True
        if not gemini_cloud.default_model or _is_legacy_gemini_text_model(gemini_cloud.default_model):
            gemini_cloud.default_model = os.getenv("GEMINI_TEXT_MODEL", DEFAULT_GEMINI_TEXT_MODEL)
            changed = True
        if gemini_cloud.default_realtime_model:
            gemini_cloud.default_realtime_model = ""
            changed = True
        if gemini_cloud.default_voice:
            gemini_cloud.default_voice = ""
            changed = True
        if not gemini_cloud.default_tts_model or _is_legacy_gemini_tts_model(gemini_cloud.default_tts_model):
            gemini_cloud.default_tts_model = os.getenv("GEMINI_TTS_MODEL", DEFAULT_GEMINI_TTS_MODEL)
            changed = True

    google_stt = config.integration("google-cloud-stt")
    if google_stt:
        if google_stt.name != "Google Cloud STT":
            google_stt.name = "Google Cloud STT"
            changed = True
        if not google_stt.default_stt_model:
            google_stt.default_stt_model = "latest_short"
            changed = True
        if not google_stt.credentials_path and os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            google_stt.credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
            google_stt.enabled = True
            changed = True

    google_tts = config.integration("google-cloud-tts")
    if google_tts:
        if google_tts.name in {"Google Cloud TTS", "Google Cloud TTS HTTP fallback"}:
            google_tts.name = "Google Cloud TTS HTTP"
            changed = True
        if not google_tts.default_voice:
            google_tts.default_voice = os.getenv("GOOGLE_TTS_VOICE", DEFAULT_GOOGLE_TTS_VOICE)
            changed = True
        if not google_tts.credentials_path and os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            google_tts.credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
            google_tts.enabled = True
            changed = True

    google_streaming_tts = config.integration("google-streaming-tts")
    if google_streaming_tts:
        if not google_streaming_tts.default_model:
            google_streaming_tts.default_model = "google-streaming-tts"
            changed = True
        if not google_streaming_tts.default_voice:
            google_streaming_tts.default_voice = os.getenv("GOOGLE_TTS_VOICE", DEFAULT_GOOGLE_TTS_VOICE)
            changed = True
        if not google_streaming_tts.credentials_path and os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            google_streaming_tts.credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
            google_streaming_tts.enabled = True
            changed = True

    web_search = config.integration("web-search")
    if web_search:
        if web_search.provider_id != "gemini-cloud":
            web_search.provider_id = "gemini-cloud"
            changed = True
        if not web_search.default_model:
            web_search.default_model = os.getenv("GEMINI_TEXT_MODEL", DEFAULT_GEMINI_TEXT_MODEL)
            changed = True
        google_key = os.getenv("GOOGLE_API_KEY", "")
        if google_key and not web_search.api_key:
            web_search.api_key = google_key
            changed = True

    google_imagen = config.integration("google-imagen")
    if google_imagen:
        if google_imagen.name != "Google Imagen":
            google_imagen.name = "Google Imagen"
            changed = True
        if not google_imagen.default_model:
            google_imagen.default_model = os.getenv("GOOGLE_IMAGEN_MODEL", DEFAULT_GOOGLE_IMAGEN_MODEL)
            changed = True
        if os.getenv("GOOGLE_API_KEY") and not google_imagen.api_key:
            google_imagen.api_key = os.getenv("GOOGLE_API_KEY", "")
            google_imagen.enabled = True
            changed = True

    ha_mcp_server = config.integration("ha-mcp-server")
    if ha_mcp_server:
        if ha_mcp_server.name != "HA MCP Server Add-on":
            ha_mcp_server.name = "HA MCP Server Add-on"
            changed = True
        if ha_mcp_server.kind != "ha_mcp":
            ha_mcp_server.kind = "ha_mcp"
            changed = True
        if os.getenv("HA_MCP_SERVER_URL") and not ha_mcp_server.base_url:
            ha_mcp_server.base_url = os.getenv("HA_MCP_SERVER_URL", "")
            changed = True
        if ha_mcp_server.token or ha_mcp_server.api_key:
            ha_mcp_server.token = ""
            ha_mcp_server.api_key = ""
            changed = True

    return changed


def _sanitize_raw_config_dict(data: dict[str, Any], default: RuntimeConfig) -> bool:
    """Sanitize raw persisted JSON before Pydantic validation."""

    changed = False
    if "openai_api_key" in data:
        data.pop("openai_api_key", None)
        changed = True

    raw_integrations = data.get("integrations")
    if isinstance(raw_integrations, list):
        filtered = [
            item
            for item in raw_integrations
            if isinstance(item, dict) and item.get("kind") in ALLOWED_INTEGRATION_KINDS
        ]
        if len(filtered) != len(raw_integrations):
            changed = True
        if not filtered:
            filtered = [item.model_dump() for item in default.integrations]
            changed = True
        data["integrations"] = filtered

    raw_flows = data.get("flows")
    if isinstance(raw_flows, list):
        for flow in raw_flows:
            if not isinstance(flow, dict):
                continue
            template = flow.get("pipeline_template")
            if template not in ALLOWED_PIPELINE_TEMPLATES:
                mode = flow.get("mode", "realtime")
                flow["pipeline_template"] = (
                    "gemini_composed_home"
                    if mode in {"composed", "classic"}
                    else "gemini_live_home"
                )
                changed = True

    return changed
