"""Gemini and Google Cloud model discovery and static model catalog helpers."""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from app.config import (
    DEFAULT_GEMINI_LIVE_MODEL,
    DEFAULT_GEMINI_TEXT_MODEL,
    DEFAULT_GEMINI_TTS_MODEL,
    DEFAULT_GOOGLE_IMAGEN_MODEL,
    DEFAULT_GOOGLE_TTS_VOICE,
    DEFAULT_WEB_SEARCH_MODEL,
    IntegrationConfig,
)


def _static_models_for(integration: IntegrationConfig, capability: str) -> list[dict[str, str]]:
    values: list[str] = []
    if integration.kind == "gemini":
        if capability == "realtime":
            values = [
                integration.default_realtime_model or DEFAULT_GEMINI_LIVE_MODEL,
                "gemini-2.0-flash-exp",
                "gemini-3.8-live",
            ]
    elif integration.kind == "gemini_cloud":
        if capability == "llm":
            values = [
                integration.default_model or DEFAULT_GEMINI_TEXT_MODEL,
                "gemini-2.5-flash",
                "gemini-2.5-pro",
                "gemini-3.8-flash",
            ]
        elif capability == "tts":
            values = [integration.default_tts_model or DEFAULT_GEMINI_TTS_MODEL, DEFAULT_GEMINI_TTS_MODEL]
    elif integration.kind == "google_cloud_stt":
        values = [integration.default_stt_model or "latest_short", "latest_short", "latest_long"]
    elif integration.kind in {"google_cloud_tts", "google_streaming_tts"}:
        values = [integration.default_voice or DEFAULT_GOOGLE_TTS_VOICE]
    elif integration.kind == "web_search":
        values = [integration.default_model or DEFAULT_WEB_SEARCH_MODEL, DEFAULT_WEB_SEARCH_MODEL]
    elif integration.kind == "google_imagen":
        values = [integration.default_model or DEFAULT_GOOGLE_IMAGEN_MODEL, DEFAULT_GOOGLE_IMAGEN_MODEL]
    else:
        values = [value for value in (integration.default_model, integration.default_realtime_model) if value]
    seen = set()
    return [
        {"id": value, "label": value}
        for value in values
        if value and not (value in seen or seen.add(value))
    ]


async def fetch_integration_models(
    integration: IntegrationConfig,
    capability: str = "llm",
) -> dict[str, Any]:
    fallback = _static_models_for(integration, capability)
    try:
        if integration.kind in {"gemini", "gemini_cloud"} and integration.api_key:
            if integration.kind == "gemini_cloud" and capability == "stt":
                return {"ok": False, "models": fallback}
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": integration.api_key},
                )
                response.raise_for_status()
            models = []
            for item in response.json().get("models", []):
                name = str(item.get("name", ""))
                methods = item.get("supportedGenerationMethods", [])
                if integration.kind == "gemini" and capability == "realtime" and "live" not in name.lower():
                    continue
                if integration.kind == "gemini_cloud" and capability == "realtime":
                    continue
                if integration.kind == "gemini_cloud" and capability == "tts" and "tts" not in name.lower():
                    continue
                if integration.kind == "gemini_cloud" and capability == "llm" and "tts" in name.lower():
                    continue
                if integration.kind == "gemini_cloud" and "generateContent" not in methods:
                    continue
                if integration.kind == "gemini" and capability != "realtime":
                    continue
                models.append(name.removeprefix("models/") if integration.kind == "gemini" else name)
            if models:
                return {"ok": True, "models": [{"id": item, "label": item} for item in sorted(models)]}
    except Exception as err:
        logger.debug("Model list fetch failed for {}: {}", integration.id, err)

    return {"ok": False, "models": fallback}
