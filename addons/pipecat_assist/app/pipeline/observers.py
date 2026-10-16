"""Pipeline observers, end-of-conversation detection, and transport parameter helpers."""

from __future__ import annotations

from typing import Any

from pipecat.processors.frameworks.rtvi import RTVIObserverParams
from pipecat.transports.base_transport import TransportParams

from app.config import FlowConfig, RuntimeConfig
from app.va_pipecat import websocket_transport_params
from app.va_pipecat_protocol import VaPipecatProtocol


def _should_end_conversation(*texts: str | None) -> bool:
    """Language-agnostic conversation end callback (no hardcoded language phrase lists)."""

    _ = texts
    return False


def _va_pipecat_protocol(config: RuntimeConfig) -> VaPipecatProtocol:
    """Build the ESPHome protocol contract from persisted runtime settings."""

    return VaPipecatProtocol(
        _should_end_conversation,
        follow_up_ms=config.esphome_follow_up_ms,
        follow_up_open_delay_ms=config.esphome_follow_up_open_delay_ms,
        wake_open_delay_ms=config.esphome_wake_open_delay_ms,
        playback_prebuffer_ms=config.esphome_playback_prebuffer_ms,
    )


def _transport_params(config: RuntimeConfig, flow: FlowConfig) -> dict[str, Any]:
    return {
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            video_in_enabled=flow.video_enabled,
        ),
        "websocket": lambda: websocket_transport_params(
            _should_end_conversation,
            follow_up_ms=config.esphome_follow_up_ms,
            follow_up_open_delay_ms=config.esphome_follow_up_open_delay_ms,
            wake_open_delay_ms=config.esphome_wake_open_delay_ms,
            playback_prebuffer_ms=config.esphome_playback_prebuffer_ms,
        ),
    }


def _rtvi_observer_params() -> RTVIObserverParams:
    """Expose live transcript and assistant text events to the WebRTC clients."""

    return RTVIObserverParams(
        bot_output_enabled=True,
        bot_llm_enabled=True,
        bot_tts_enabled=True,
        bot_speaking_enabled=True,
        user_llm_enabled=True,
        user_speaking_enabled=True,
        user_transcription_enabled=True,
        metrics_enabled=True,
    )
