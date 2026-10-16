"""Pipecat service builders and pipeline flow validation helpers."""

from __future__ import annotations

import os
import time
from typing import Any

from loguru import logger
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.transcriptions.language import Language

from app.config import (
    COMPOSED_STEP_PROVIDER_KINDS,
    DEFAULT_GEMINI_LIVE_MODEL,
    DEFAULT_GEMINI_LIVE_VOICE,
    DEFAULT_GEMINI_TEXT_MODEL,
    DEFAULT_GOOGLE_TTS_VOICE,
    DEFAULT_WEB_SEARCH_MODEL,
    GEMINI_LIVE_VOICES,
    FlowConfig,
    IntegrationConfig,
    RuntimeConfig,
)
from app.services.google_credentials import _require_integration
from app.web_search_tool import run_gemini_web_search, web_search_schema


def _runtime_language(
    flow: FlowConfig,
    integration: IntegrationConfig | None,
    override: str | None = None,
) -> str:
    return (
        (override or "").strip()
        or ((integration.language or "").strip() if integration else "")
        or (flow.language or "").strip()
    )


def _pipecat_language(language: str | None) -> Language | str | None:
    value = (language or "").strip()
    if not value or value.lower() == "pipecat-assist":
        return None
    normalized = value.replace("_", "-").lower()
    for candidate in Language:
        if candidate.value.lower() == normalized:
            return candidate
    base = normalized.split("-", 1)[0]
    for candidate in Language:
        if candidate.value.lower() == base:
            return candidate
    return value


def _runtime_speed(flow: FlowConfig, integration: IntegrationConfig | None) -> float:
    return float((integration.speed if integration else None) or flow.speed or 1.0)


def _tts_text_aggregation_mode(integration: IntegrationConfig | None):
    if not integration or integration.tts_streaming_mode != "token":
        return None
    if integration.kind != "google_streaming_tts":
        return None
    from pipecat.services.tts_service import TextAggregationMode

    return TextAggregationMode.TOKEN


def _output_step(flow: FlowConfig):
    return next(
        (step for step in flow.steps if step.kind in {"output", "tts"} and step.enabled),
        None,
    )


def _enabled_step(flow: FlowConfig, kind: str):
    return next((step for step in flow.steps if step.kind == kind and step.enabled), None)


def _step_integration(
    config: RuntimeConfig,
    flow: FlowConfig,
    kind: str,
) -> tuple[Any, IntegrationConfig | None]:
    step = _enabled_step(flow, kind)
    if not step:
        return None, None
    return step, config.integration(step.integration_id)


def _configured_integration(integration: IntegrationConfig | None) -> bool:
    return bool(integration and integration.enabled)


def _enabled_web_search_step(flow: FlowConfig):
    return _enabled_step(flow, "web_search")


def _web_search_enabled(flow: FlowConfig) -> bool:
    return bool(flow.web_search_enabled or _enabled_web_search_step(flow))


def _memory_enabled(config: RuntimeConfig, flow: FlowConfig) -> bool:
    memory_step = _enabled_step(flow, "memory")
    return config.session_memory_enabled and (flow.memory_enabled or memory_step is not None)


def _effective_instructions(flow: FlowConfig) -> str:
    return flow.instructions


def _web_search_tool_schema(config: RuntimeConfig, flow: FlowConfig) -> FunctionSchema | None:
    """Return the optional web search tool schema for a flow."""

    if not _web_search_enabled(flow):
        return None
    integration = config.integration("web-search")
    if not integration or not integration.enabled:
        return None
    provider = config.integration(integration.provider_id or "gemini-cloud") or config.integration("gemini-cloud")
    if not provider:
        logger.warning("Web search is enabled, but no search LLM provider is selected")
        return None
    model = (integration.default_model or provider.default_model or DEFAULT_WEB_SEARCH_MODEL).strip()
    if not model.removeprefix("models/").startswith("gemini-"):
        model = provider.default_model or DEFAULT_GEMINI_TEXT_MODEL

    if provider.kind in {"gemini", "gemini_cloud"}:
        api_key = (provider.api_key or integration.api_key or os.getenv("GOOGLE_API_KEY", "")).strip()
        if not api_key:
            logger.warning("Web search is enabled, but {} has no API key", provider.name)
            return None

        async def runner(query: str) -> str:
            return await run_gemini_web_search(api_key, model or DEFAULT_GEMINI_TEXT_MODEL, query)

        return web_search_schema(runner, model or DEFAULT_GEMINI_TEXT_MODEL)

    logger.warning("Web search provider {} is not supported yet", provider.kind)
    return None


def _merge_tools_schema(
    base_schema,
    extra_tools: list[FunctionSchema] | None = None,
) -> ToolsSchema | None:
    """Return one ToolsSchema with MCP and local assistant tools."""

    tools: list[FunctionSchema] = []
    if base_schema and getattr(base_schema, "standard_tools", None):
        tools.extend(base_schema.standard_tools)
    tools.extend(extra_tools or [])
    return ToolsSchema(standard_tools=tools) if tools else None


def _register_local_tool_handlers(llm, tools: list[FunctionSchema]) -> None:
    """Register local function handlers when the LLM service needs explicit callbacks."""

    for schema in tools:
        handler = getattr(schema, "handler", None)
        if handler:
            llm.register_function(schema.name, handler)


def _step_model(step, integration: IntegrationConfig | None, fallback: str = "") -> str:
    return (
        (getattr(step, "model", "") if step else "")
        or (integration.default_model if integration else "")
        or fallback
    ).strip()


def _step_model_for(
    step,
    integration: IntegrationConfig | None,
    capability: str,
    fallback: str = "",
) -> str:
    if getattr(step, "model", ""):
        return step.model.strip()
    if not integration:
        return fallback.strip()
    if capability == "stt":
        return (integration.default_stt_model or fallback).strip()
    if capability == "tts":
        return (integration.default_tts_model or integration.default_model or fallback).strip()
    if capability == "realtime":
        return (integration.default_realtime_model or fallback).strip()
    return (integration.default_model or fallback).strip()


def _step_realtime_model(step, integration: IntegrationConfig | None, fallback: str = "") -> str:
    return (
        (getattr(step, "model", "") if step else "")
        or (integration.default_realtime_model if integration else "")
        or fallback
    ).strip()


def _step_voice(step, integration: IntegrationConfig | None, fallback: str = "") -> str:
    return (
        (getattr(step, "voice", "") if step else "")
        or (integration.default_voice if integration else "")
        or fallback
    ).strip()


def _runtime_mode(flow: FlowConfig, provider_kind: str | None = None) -> str:
    if flow.mode in {"composed", "classic"}:
        return "composed"
    if flow.pipeline_template == "gemini_composed_home":
        return "composed"
    return "s2s"


def _provider_names_for_kinds(config: RuntimeConfig, kinds: set[str]) -> str:
    names = sorted({item.name for item in config.integrations if item.kind in kinds})
    return ", ".join(names or sorted(kinds))


def _runtime_flow_errors(config: RuntimeConfig, flow: FlowConfig) -> list[str]:
    """Return pipeline errors that would prevent a WebRTC runtime from starting."""

    errors: list[str] = []
    if not flow.enabled:
        errors.append(f"Pipeline {flow.name} is disabled.")

    model_integration = config.model_integration(flow)
    provider_kind = model_integration.kind if model_integration else flow.provider_id
    runtime_mode = _runtime_mode(flow, provider_kind)

    if runtime_mode == "s2s":
        if provider_kind not in {"gemini", "gemini_live"}:
            errors.append(f"Realtime speech-to-speech runtime for {provider_kind} is not supported. Use Google Gemini.")
        if any(step.kind in {"stt", "tts"} and step.enabled for step in flow.steps):
            errors.append("Speech-to-speech pipelines cannot use separate STT or TTS steps.")
        return errors

    role_labels = {"stt": "STT", "llm": "LLM", "tts": "TTS"}
    for kind, supported_kinds in COMPOSED_STEP_PROVIDER_KINDS.items():
        step, integration = _step_integration(config, flow, kind)
        label = role_labels[kind]
        if not step:
            errors.append(f"Composed pipeline needs an enabled {label} step.")
            continue
        if not step.integration_id:
            errors.append(f"{step.label or label} needs an integration.")
            continue
        if not integration:
            errors.append(f"{step.label or label} uses missing integration {step.integration_id}.")
            continue
        if integration.kind not in supported_kinds:
            supported_names = _provider_names_for_kinds(config, supported_kinds)
            if kind == "stt" and integration.kind == "gemini_cloud":
                errors.append(
                    "Google Gemini Cloud can transcribe buffered HA Assist audio, but it is not "
                    f"a composed realtime STT processor. Use one of: {supported_names}."
                )
            else:
                errors.append(
                    f"{integration.name} cannot be used as {label} in composed realtime pipelines. "
                    f"Use one of: {supported_names}."
                )
            continue
        if not integration.enabled:
            errors.append(f"{integration.name} is disabled.")

    return errors


def _realtime_model_matches_provider(provider_kind: str, model: str) -> bool:
    model = (model or "").strip()
    if not model:
        return False
    if provider_kind in {"gemini", "gemini_live"}:
        return "gemini" in model
    return True


def _realtime_voice_matches_provider(provider_kind: str, voice: str) -> bool:
    voice = (voice or "").strip()
    if not voice:
        return False
    if provider_kind in {"gemini", "gemini_live"}:
        return voice in GEMINI_LIVE_VOICES
    return True


def _model_name(
    flow: FlowConfig,
    integration: IntegrationConfig | None,
    provider_kind: str | None = None,
) -> str:
    model_step = flow.model_step()
    model = (
        (model_step.model if model_step else "")
        or flow.model
        or (integration.default_realtime_model if integration else "")
    ).strip()
    kind = integration.kind if integration else provider_kind
    if kind in {"gemini", "gemini_live"} and not _realtime_model_matches_provider(
        "gemini",
        model,
    ):
        return (integration.default_realtime_model if integration else "") or DEFAULT_GEMINI_LIVE_MODEL
    return model


def _gemini_model(model: str) -> str:
    model = model.strip() or DEFAULT_GEMINI_LIVE_MODEL
    return model if model.startswith("models/") else f"models/{model}"


def _gemini_voice(flow: FlowConfig, integration: IntegrationConfig | None) -> str:
    output_step = _output_step(flow)
    for candidate in (
        output_step.voice if output_step else "",
        flow.voice,
        integration.default_voice if integration else "",
    ):
        if _realtime_voice_matches_provider("gemini", candidate):
            return candidate
    return DEFAULT_GEMINI_LIVE_VOICE


def _gemini_vad(flow: FlowConfig, *, local_turn_detection: bool = False):
    try:
        from pipecat.services.google.gemini_live.llm import GeminiVADParams
    except ImportError:
        from pipecat.services.google.gemini_live import GeminiVADParams

    if local_turn_detection:
        # Local Silero VAD emits the activity_start/activity_end markers. Leaving
        # Gemini server VAD enabled at the same time creates two competing turn
        # detectors and duplicate interruptions.
        return GeminiVADParams(disabled=True)

    silence_by_eagerness = {
        "high": 300,
        "medium": 500,
        "low": 800,
    }
    silence_duration_ms = silence_by_eagerness.get(flow.vad_eagerness)
    return GeminiVADParams(silence_duration_ms=silence_duration_ms)


def _composed_vad_analyzer(flow: FlowConfig):
    """Return local VAD tuned for browser and ESP32 composed pipelines."""

    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams

    stop_secs_by_eagerness = {
        "high": 0.2,
        "medium": 0.2,
        "auto": 0.2,
        "low": 0.2,
    }
    return SileroVADAnalyzer(
        params=VADParams(
            confidence=0.7,
            start_secs=0.12,
            stop_secs=stop_secs_by_eagerness.get(flow.vad_eagerness, 0.45),
            min_volume=0.02,
        )
    )


def _gemini_live_service(
    *,
    api_key: str,
    model: str,
    flow: FlowConfig,
    integration: IntegrationConfig | None,
    local_turn_detection: bool = False,
    inference_on_context_initialization: bool = True,
):
    try:
        from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService
    except ImportError:
        from pipecat.services.google.gemini_live import GeminiLiveLLMService

    class ResilientGeminiLiveLLMService(GeminiLiveLLMService):
        """Do not count a normal long-lived session renewal as a rapid failure."""

        async def _handle_user_started_speaking(self, frame):
            logger.debug(
                "Gemini local turn start: vad_disabled={} ready={} session={}",
                getattr(self, "_vad_disabled", False),
                getattr(self, "_ready_for_realtime_input", False),
                bool(getattr(self, "_session", None)),
            )
            await super()._handle_user_started_speaking(frame)

        async def _handle_user_stopped_speaking(self, frame):
            logger.debug(
                "Gemini local turn stop: vad_disabled={} ready={} session={}",
                getattr(self, "_vad_disabled", False),
                getattr(self, "_ready_for_realtime_input", False),
                bool(getattr(self, "_session", None)),
            )
            await super()._handle_user_stopped_speaking(frame)

        async def _handle_connection_error(self, error: Exception) -> bool:
            connection_started = getattr(self, "_connection_start_time", None)
            if (
                connection_started
                and time.time() - connection_started >= 30.0
                and getattr(self, "_consecutive_failures", 0)
            ):
                logger.info(
                    "Gemini connection was stable before renewal; resetting failure counter"
                )
                self._consecutive_failures = 0
            return await super()._handle_connection_error(error)

    settings_kwargs: dict[str, Any] = {
        "model": _gemini_model(model),
        "system_instruction": _effective_instructions(flow),
        "voice": _gemini_voice(flow, integration),
    }
    if _enabled_step(flow, "vad"):
        settings_kwargs["vad"] = _gemini_vad(
            flow,
            local_turn_detection=local_turn_detection,
        )
    if flow.max_output_tokens:
        settings_kwargs["max_tokens"] = flow.max_output_tokens

    return ResilientGeminiLiveLLMService(
        api_key=api_key,
        settings=ResilientGeminiLiveLLMService.Settings(**settings_kwargs),
        inference_on_context_initialization=inference_on_context_initialization,
    )


def _build_stt_service(
    config: RuntimeConfig,
    flow: FlowConfig,
    language_override: str | None = None,
):
    step, integration = _step_integration(config, flow, "stt")
    integration = _require_integration(integration, "STT", fields=())
    model = _step_model_for(step, integration, "stt")
    language = _runtime_language(flow, integration, language_override)
    logger.info(
        "Building composed STT service integration={} kind={} model={} language={}",
        integration.name,
        integration.kind,
        model or "",
        language,
    )

    if integration.kind == "google_cloud_stt":
        from pipecat.services.google.stt import GoogleSTTService

        stt_settings: dict[str, Any] = {"model": model or None}
        pipecat_lang = _pipecat_language(language)
        if pipecat_lang is not None:
            stt_settings["language"] = pipecat_lang
        return GoogleSTTService(
            credentials=integration.credentials_json or None,
            credentials_path=integration.credentials_path or None,
            settings=GoogleSTTService.Settings(**stt_settings),
        )

    raise RuntimeError(f"STT provider {integration.kind} is not supported by composed runtime")


def _build_llm_service(config: RuntimeConfig, flow: FlowConfig, tools_schema=None):
    step, integration = _step_integration(config, flow, "llm")
    integration = _require_integration(integration, "LLM", fields=())
    model = _step_model_for(step, integration, "llm", flow.text_model)

    if integration.kind in {"gemini", "gemini_cloud"}:
        from pipecat.services.google.llm import GoogleLLMService

        api_key = integration.api_key or os.getenv("GOOGLE_API_KEY", "")
        if not api_key:
            raise RuntimeError("Google Gemini is missing api_key")
        return GoogleLLMService(
            api_key=api_key,
            settings=GoogleLLMService.Settings(
                model=model or integration.default_model or DEFAULT_GEMINI_TEXT_MODEL,
                system_instruction=_effective_instructions(flow),
                max_tokens=flow.max_output_tokens or 4096,
            ),
        )

    raise RuntimeError(f"LLM provider {integration.kind} is not supported by composed runtime")


def _build_tts_service(config: RuntimeConfig, flow: FlowConfig):
    step, integration = _step_integration(config, flow, "tts")
    integration = _require_integration(integration, "TTS", fields=())
    model = _step_model_for(step, integration, "tts")
    voice = _step_voice(step, integration)
    speed = _runtime_speed(flow, integration)
    text_aggregation_mode = _tts_text_aggregation_mode(integration)

    if integration.kind == "google_cloud_tts":
        from pipecat.services.google.tts import GoogleHttpTTSService

        if not integration.credentials_json and not integration.credentials_path:
            raise RuntimeError("Google Cloud TTS is missing service account credentials")
        return GoogleHttpTTSService(
            credentials=integration.credentials_json or None,
            credentials_path=integration.credentials_path or None,
            location=integration.location or None,
            settings=GoogleHttpTTSService.Settings(
                voice=voice or DEFAULT_GOOGLE_TTS_VOICE,
                speaking_rate=speed,
            ),
        )
    if integration.kind == "google_streaming_tts":
        from pipecat.services.google.tts import GoogleTTSService

        if not integration.credentials_json and not integration.credentials_path:
            raise RuntimeError("Google Cloud TTS Streaming is missing service account credentials")
        return GoogleTTSService(
            credentials=integration.credentials_json or None,
            credentials_path=integration.credentials_path or None,
            location=integration.location or None,
            text_aggregation_mode=text_aggregation_mode,
            settings=GoogleTTSService.Settings(
                voice=voice or DEFAULT_GOOGLE_TTS_VOICE,
            ),
        )

    raise RuntimeError(f"TTS provider {integration.kind} is not supported by composed runtime")
