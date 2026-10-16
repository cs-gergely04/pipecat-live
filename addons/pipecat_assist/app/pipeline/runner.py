"""Pipecat bot session runner for SmallWebRTC and ESPHome transports."""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from typing import Any

from loguru import logger
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.transports.base_transport import BaseTransport
from pipecat.workers.runner import WorkerRunner

from app.audio_debug import create_audio_debug_session
from app.config import FlowConfig, RuntimeConfig
from app.mcp_bridge import CombinedMCPBridge
from app.pipeline.builder import (
    _build_llm_service,
    _build_stt_service,
    _build_tts_service,
    _composed_vad_analyzer,
    _effective_instructions,
    _enabled_step,
    _gemini_live_service,
    _memory_enabled,
    _merge_tools_schema,
    _model_name,
    _register_local_tool_handlers,
    _runtime_flow_errors,
    _runtime_mode,
    _step_integration,
    _step_model,
    _web_search_tool_schema,
)
from app.pipeline.flows import _flow_enabled, _flow_node_configs
from app.pipeline.observers import _rtvi_observer_params, _transport_params
from app.session_memory import SESSION_MEMORY
from app.state import STORE


def _runner_body(runner_args: RunnerArguments) -> dict[str, Any]:
    body = runner_args.body if isinstance(runner_args.body, dict) else {}
    request_data = body.get("request_data")
    return request_data if isinstance(request_data, dict) else body


def _session_client_id(runner_args: RunnerArguments, flow: FlowConfig) -> str:
    body = _runner_body(runner_args)
    for key in ("client_id", "device_id", "satellite_id", "source"):
        value = str(body.get(key, "")).strip()
        if value:
            return f"{flow.id}:{value}"
    return f"{flow.id}:default"


async def run_bot(
    transport: BaseTransport,
    runner_args: RunnerArguments,
    config: RuntimeConfig,
    flow: FlowConfig,
    mcp_token: str = "",
):
    """Run one Pipecat session."""

    integration = config.model_integration(flow)
    provider_kind = integration.kind if integration else flow.provider_id
    runtime_mode = _runtime_mode(flow, provider_kind)
    session_body = _runner_body(runner_args)
    is_esphome_satellite = session_body.get("transport") == "va-pipecat"
    client_id = _session_client_id(runner_args, flow)
    language_override = str(session_body.get("language") or "").strip() or None

    bridge: CombinedMCPBridge | None = None
    mcp_tools_schema = None
    mcp_servers = config.enabled_mcp_servers(mcp_token) if flow.mcp_enabled else []
    if mcp_servers:
        bridge = CombinedMCPBridge(mcp_servers, flow.mcp_tool_allowlist)
        try:
            await bridge.start()
            mcp_tools_schema = await bridge.tools_schema(
                cache_enabled=config.mcp_tools_cache_enabled,
                cache_ttl_seconds=config.mcp_tools_cache_ttl_seconds,
            )
            if not mcp_tools_schema.standard_tools:
                mcp_tools_schema = None
        except asyncio.CancelledError as err:
            with suppress(Exception):
                await bridge.close()
            bridge = None
            raise RuntimeError(f"MCP tools are enabled but startup was cancelled: {err}") from err
        except Exception as err:
            with suppress(Exception):
                await bridge.close()
            bridge = None
            raise RuntimeError(f"MCP tools are enabled but unavailable: {err}") from err

    local_tool_schemas = [schema for schema in [_web_search_tool_schema(config, flow)] if schema]
    tools_schema = _merge_tools_schema(mcp_tools_schema, local_tool_schemas)
    context_for_memory = None
    audio_debug = None

    if runtime_mode == "s2s":
        if provider_kind not in {"gemini", "gemini_live"}:
            raise RuntimeError(
                f"Realtime speech-to-speech runtime for {provider_kind} is not supported. Use Google Gemini."
            )

        api_key = (integration.api_key if integration else "") or os.getenv("GOOGLE_API_KEY", "")
        if not api_key:
            raise RuntimeError("The selected Gemini realtime provider is missing an API key")

        realtime_model = _model_name(flow, integration, provider_kind)
        llm = _gemini_live_service(
            api_key=api_key,
            model=realtime_model,
            flow=flow,
            integration=integration,
            local_turn_detection=bool(_enabled_step(flow, "vad")),
            inference_on_context_initialization=not is_esphome_satellite,
        )
        _register_local_tool_handlers(llm, local_tool_schemas)

        logger.info(
            "Starting Gemini Live speech-to-speech model {} for flow {}",
            realtime_model,
            flow.id,
        )

        if bridge and mcp_tools_schema:
            await bridge.register_tools_schema(mcp_tools_schema, llm)

        greeting_messages = (
            [{"role": "developer", "content": flow.greeting}]
            if flow.greeting.strip()
            else []
        )
        context_messages = SESSION_MEMORY.restore(
            client_id,
            greeting_messages,
            enabled=_memory_enabled(config, flow),
            reuse_seconds=config.session_memory_reuse_seconds,
            max_messages=config.session_memory_max_messages,
        )
        context = (
            LLMContext(context_messages, tools_schema)
            if tools_schema
            else LLMContext(context_messages)
        )
        context_for_memory = context
        user_params = None
        if provider_kind == "gemini" and _enabled_step(flow, "vad"):
            from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
            from pipecat.turns.user_turn_strategies import UserTurnStrategies

            user_params = LLMUserAggregatorParams(
                vad_analyzer=_composed_vad_analyzer(flow),
                user_turn_strategies=UserTurnStrategies(
                    stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.2)]
                ),
            )
        user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
            context,
            user_params=user_params,
            realtime_service_mode=True,
        )

        if config.audio_debug_enabled:
            try:
                audio_debug = create_audio_debug_session(
                    config,
                    flow,
                    provider_kind,
                    realtime_model,
                )
            except Exception as err:
                logger.warning("Audio debug recorder could not start: {}", err)
        processors = [transport.input()]
        if audio_debug:
            processors.append(audio_debug.input_recorder)
        processors.extend([user_aggregator, llm])
        if audio_debug:
            processors.append(audio_debug.output_recorder)
        processors.extend([transport.output(), assistant_aggregator])

        pipeline = Pipeline(processors)
        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
            rtvi_observer_params=_rtvi_observer_params(),
            idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
        )

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("Client connected to flow {}", flow.id)
            if _flow_enabled(flow):
                logger.warning("Pipecat Flows are ignored for speech-to-speech runtime {}", provider_kind)
            if is_esphome_satellite or flow.greeting.strip():
                await worker.queue_frames([LLMRunFrame()])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("Client disconnected from flow {}", flow.id)
            await worker.cancel()

    else:
        from pipecat.processors.audio.vad_processor import VADProcessor
        from pipecat.turns.user_stop import SpeechTimeoutUserTurnStopStrategy
        from pipecat.turns.user_turn_strategies import UserTurnStrategies

        stt = _build_stt_service(config, flow, language_override=language_override)
        llm = _build_llm_service(config, flow, tools_schema=tools_schema)
        _register_local_tool_handlers(llm, local_tool_schemas)
        tts = _build_tts_service(config, flow)

        _, stt_integration = _step_integration(config, flow, "stt")
        llm_step, llm_integration = _step_integration(config, flow, "llm")
        _, tts_integration = _step_integration(config, flow, "tts")
        llm_model = _step_model(llm_step, llm_integration, flow.text_model)
        provider_label = "+".join(
            item.kind
            for item in (stt_integration, llm_integration, tts_integration)
            if item is not None
        )
        logger.info(
            "Starting composed realtime pipeline {} with LLM {} for flow {}",
            provider_label,
            llm_model,
            flow.id,
        )

        initial_flow_node = None
        active_tools_schema = None if _flow_enabled(flow) else tools_schema
        context_messages = [{"role": "developer", "content": _effective_instructions(flow)}]
        if flow.greeting.strip():
            context_messages.append({"role": "developer", "content": flow.greeting})
        context_messages = SESSION_MEMORY.restore(
            client_id,
            context_messages,
            enabled=_memory_enabled(config, flow),
            reuse_seconds=config.session_memory_reuse_seconds,
            max_messages=config.session_memory_max_messages,
        )
        context = LLMContext(context_messages, active_tools_schema) if active_tools_schema else LLMContext(context_messages)
        context_for_memory = context
        vad_step = _enabled_step(flow, "vad")
        vad_processor = VADProcessor(vad_analyzer=_composed_vad_analyzer(flow)) if vad_step else None
        if vad_step:
            context_aggregator = LLMContextAggregatorPair(
                context,
                user_params=LLMUserAggregatorParams(
                    user_turn_strategies=UserTurnStrategies(
                        stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=0.2)]
                    ),
                    user_turn_stop_timeout=2.0,
                ),
            )
        else:
            context_aggregator = LLMContextAggregatorPair(context)

        if bridge and mcp_tools_schema and not _flow_enabled(flow):
            await bridge.register_tools_schema(mcp_tools_schema, llm)

        if _flow_enabled(flow):
            initial_flow_node = _flow_node_configs(flow, bridge)

        if config.audio_debug_enabled:
            try:
                audio_debug = create_audio_debug_session(
                    config,
                    flow,
                    provider_label or "composed",
                    llm_model,
                )
            except Exception as err:
                logger.warning("Audio debug recorder could not start: {}", err)

        processors = [transport.input()]
        if vad_processor:
            processors.append(vad_processor)
        if audio_debug:
            processors.append(audio_debug.input_recorder)
        processors.extend([stt, context_aggregator.user(), llm, tts])
        if audio_debug:
            processors.append(audio_debug.output_recorder)
        processors.extend([transport.output(), context_aggregator.assistant()])

        pipeline = Pipeline(processors)
        worker = PipelineWorker(
            pipeline,
            params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
            rtvi_observer_params=_rtvi_observer_params(),
            idle_timeout_secs=runner_args.pipeline_idle_timeout_secs,
        )
        flow_manager = None
        if initial_flow_node:
            from pipecat_flows import FlowManager

            flow_manager = FlowManager(
                worker=worker,
                llm=llm,
                context_aggregator=context_aggregator,
                transport=transport,
            )

        @transport.event_handler("on_client_connected")
        async def on_client_connected(transport, client):
            logger.info("Client connected to composed flow {}", flow.id)
            if flow_manager and initial_flow_node:
                await flow_manager.initialize(initial_flow_node)
            elif flow.greeting.strip() and not is_esphome_satellite:
                await worker.queue_frames([LLMRunFrame()])

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("Client disconnected from flow {}", flow.id)
            await worker.cancel()

    try:
        runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
        await runner.add_workers(worker)
        await runner.run()
    finally:
        if context_for_memory:
            SESSION_MEMORY.cache(
                client_id,
                context_for_memory,
                enabled=_memory_enabled(config, flow),
                max_messages=config.session_memory_max_messages,
            )
        if bridge:
            await bridge.close()
        if audio_debug:
            with suppress(Exception):
                audio_debug.close()


async def bot(runner_args: RunnerArguments):
    """Pipecat runner entry point."""

    config = STORE.load()
    body = _runner_body(runner_args)
    flow = config.selected_flow(body.get("flow_id"))
    flow_errors = _runtime_flow_errors(config, flow)
    if flow_errors:
        raise RuntimeError(flow_errors[0])
    transport = await create_transport(runner_args, _transport_params(config, flow))
    await run_bot(transport, runner_args, config, flow, mcp_token=config.effective_mcp_token)
