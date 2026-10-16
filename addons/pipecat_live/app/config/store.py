"""Persistence store for Pipecat Assist runtime configuration."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from .constants import (
    ALLOWED_INTEGRATION_KINDS,
    CONFIG_PATH,
    DEFAULT_GEMINI_LIVE_MODEL,
    DEFAULT_GEMINI_LIVE_VOICE,
    REDACTED,
    SECRET_FIELDS,
)
from .defaults import default_config_from_environment
from .migrations import (
    _repair_composed_gemini_integrations,
    _repair_flow_provider_model,
    _repair_mcp_url_overrides,
    _repair_provider_defaults,
    _repair_text_model_defaults,
    _sanitize_raw_config_dict,
    _strip_unsupported_s2s_flow_steps,
)
from .models import IntegrationConfig, RuntimeConfig


class ConfigStore:
    """Read and write runtime configuration."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path

    def load(self) -> RuntimeConfig:
        """Load persisted config, creating it when absent."""

        default = default_config_from_environment()
        if not self.path.exists():
            legacy_path = self.path.parent / "pipecat_assist.json"
            if legacy_path.exists():
                try:
                    with legacy_path.open("r", encoding="utf-8") as file:
                        raw = json.load(file)
                    data = default.model_dump()
                    data.update(raw)
                    _sanitize_raw_config_dict(data, default)
                    config = RuntimeConfig.model_validate(data)
                    self.save(config)
                    return config
                except Exception:
                    pass
            if not default.satellite_shared_secret:
                default.satellite_shared_secret = secrets.token_urlsafe(24)
            self.save(default)
            return default

        with self.path.open("r", encoding="utf-8") as file:
            raw = json.load(file)
        data = default.model_dump()
        data.update(raw)
        changed = _sanitize_raw_config_dict(data, default)
        config = RuntimeConfig.model_validate(data)

        integration_ids = {item.id for item in config.integrations}
        for integration in default.integrations:
            if integration.id not in integration_ids:
                config.integrations.append(integration)
                integration_ids.add(integration.id)
                changed = True

        for key in ("runner_port", "esp32_mode", "log_level"):
            default_value = getattr(default, key)
            if getattr(config, key) != default_value:
                setattr(config, key, default_value)
                changed = True

        gemini = config.integration("gemini")
        gemini_cloud = config.integration("gemini-cloud")
        google_api_key = config.google_api_key or os.getenv("GOOGLE_API_KEY", "")
        if google_api_key:
            if not config.google_api_key:
                config.google_api_key = google_api_key
                changed = True
            if gemini and not gemini.api_key:
                gemini.api_key = google_api_key
                gemini.enabled = True
                changed = True
            if gemini_cloud and not gemini_cloud.api_key:
                gemini_cloud.api_key = google_api_key
                gemini_cloud.enabled = True
                changed = True

        changed = _repair_provider_defaults(config) or changed

        mcp = config.mcp_integration
        if config.ha_mcp_url and mcp and not mcp.base_url:
            mcp.base_url = config.ha_mcp_url
            changed = True
        if config.longlived_token and mcp and not mcp.token:
            mcp.token = config.longlived_token
            changed = True

        if not config.satellite_shared_secret:
            config.satellite_shared_secret = (
                default.satellite_shared_secret or secrets.token_urlsafe(24)
            )
            changed = True

        if config.version < 22:
            config.version = 22
            config.integrations = [
                item for item in config.integrations if item.kind in ALLOWED_INTEGRATION_KINDS
            ]
            for flow in config.flows:
                if flow.provider_id not in {"gemini", "gemini-cloud"}:
                    flow.provider_id = "gemini"
                    flow.model = DEFAULT_GEMINI_LIVE_MODEL
                    flow.voice = DEFAULT_GEMINI_LIVE_VOICE
                for step in flow.steps:
                    if step.kind in {"llm", "output"} and step.integration_id not in {"gemini", "gemini-cloud"}:
                        step.integration_id = "gemini"
                    elif step.kind == "web_search":
                        step.integration_id = "web-search"
                    elif step.kind == "stt" and step.integration_id not in {"google-cloud-stt", "gemini-cloud"}:
                        step.integration_id = "google-cloud-stt"
                    elif step.kind == "tts" and step.integration_id not in {"google-cloud-tts", "google-streaming-tts", "gemini-cloud"}:
                        step.integration_id = "google-streaming-tts"
            changed = True

        for flow in config.flows:
            if not flow.language:
                flow.language = "en"
                changed = True
            changed = _repair_composed_gemini_integrations(config, flow) or changed
            changed = _repair_flow_provider_model(config, flow) or changed
            changed = _strip_unsupported_s2s_flow_steps(config, flow) or changed
        changed = _repair_text_model_defaults(config) or changed
        changed = _repair_mcp_url_overrides(config) or changed

        if changed:
            self.save(config)
        return config

    def save(self, config: RuntimeConfig) -> None:
        """Persist config atomically enough for a single add-on process."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(config.model_dump(), file, indent=2, sort_keys=True)
            file.write("\n")
        tmp_path.replace(self.path)

    def reset_mcp_defaults(self) -> RuntimeConfig:
        """Reset Home Assistant MCP settings to the Supervisor-backed defaults."""

        config = self.load()
        config.ha_mcp_url = ""
        config.longlived_token = ""

        integration = config.mcp_integration
        if not integration:
            integration = IntegrationConfig(
                id="ha-mcp",
                name="Home Assistant MCP",
                kind="home_assistant_mcp",
                enabled=True,
            )
            config.integrations.append(integration)

        integration.enabled = True
        integration.base_url = ""
        integration.token = ""

        self.save(config)
        return config

    def reset_integration_defaults(self, integration_id: str) -> RuntimeConfig:
        """Reset one integration to the add-on defaults."""

        config = self.load()
        current = config.integration(integration_id)
        if not current:
            raise KeyError(integration_id)

        defaults = default_config_from_environment().integrations
        replacement = next(
            (
                item
                for item in defaults
                if item.id == current.id or item.kind == current.kind
            ),
            None,
        )
        if replacement:
            next_item = replacement.model_copy(deep=True)
            next_item.id = current.id
            next_item.name = current.name
        else:
            next_item = IntegrationConfig(
                id=current.id,
                name=current.name,
                kind=current.kind,
            )

        config.integrations = [
            next_item if item.id == integration_id else item for item in config.integrations
        ]
        self.save(config)
        return config

    def update_from_public(self, payload: dict[str, Any]) -> RuntimeConfig:
        """Apply a UI update while preserving redacted secrets."""

        current = self.load()
        data = current.model_dump()
        data.update(payload)
        _sanitize_raw_config_dict(data, current)
        for key in ("google_api_key", "longlived_token", "satellite_shared_secret"):
            incoming = payload.get(key)
            if incoming in (None, "", REDACTED):
                data[key] = getattr(current, key)

        current_integrations = {item.id: item for item in current.integrations}
        for item in data.get("integrations", []):
            current_item = current_integrations.get(item.get("id"))
            if not current_item:
                continue
            for key in SECRET_FIELDS:
                if item.get(key) in (None, "", REDACTED):
                    item[key] = getattr(current_item, key)

        config = RuntimeConfig.model_validate(data)
        _repair_provider_defaults(config)
        for flow in config.flows:
            _repair_composed_gemini_integrations(config, flow)
            _repair_flow_provider_model(config, flow)
            _strip_unsupported_s2s_flow_steps(config, flow)
        _repair_mcp_url_overrides(config)
        self.save(config)
        return config
