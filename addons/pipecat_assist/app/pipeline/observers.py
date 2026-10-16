"""Pipeline observers, end-of-conversation detection, and transport parameter helpers."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from pipecat.processors.frameworks.rtvi import RTVIObserverParams
from pipecat.transports.base_transport import TransportParams

from app.config import FlowConfig, RuntimeConfig
from app.va_pipecat import websocket_transport_params
from app.va_pipecat_protocol import VaPipecatProtocol


def _conversation_end_text(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or "").replace("ł", "l").replace("Ł", "L"))
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    ascii_text = re.sub(r"[^a-z0-9']+", " ", ascii_text)
    return re.sub(r"\s+", " ", ascii_text).strip()


def _should_end_conversation(*texts: str | None) -> bool:
    """Return true when the user or assistant clearly closes the conversation."""

    phrase_patterns = (
        r"\b(to wszystko|wystarczy|dziekuje to wszystko|dzieki to wszystko)\b",
        r"\b(dziekuje koniec|dzieki koniec|ok koniec|okej koniec|dobra koniec)\b",
        r"\b(koniec rozmowy|konczymy rozmowe|zakoncz rozmowe|zakonczmy rozmowe)\b",
        r"\b(przestan sluchac|nie sluchaj|nie nasluchuj)\b",
        r"\b(that is all|that's all|thanks that's all|thank you that's all)\b",
        r"\b(end conversation|stop listening|we are done|goodbye|bye for now)\b",
        r"\b(milego dnia|do uslyszenia|do zobaczenia|na razie)\b",
        r"\b(have a nice day|talk to you later|see you later)\b",
    )
    short_end_pattern = re.compile(
        r"^(?:ok|okej|dobra|no|dziekuje|dzieki|thanks|thank you)?\s*"
        r"(?:koniec|wystarczy|goodbye|bye)\s*$"
    )
    for text in texts:
        clean = _conversation_end_text(text)
        if not clean:
            continue
        if short_end_pattern.search(clean):
            return True
        if any(re.search(pattern, clean) for pattern in phrase_patterns):
            return True
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
