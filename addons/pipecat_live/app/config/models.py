"""Pydantic models for Pipecat Assist runtime configuration."""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .constants import (
    DEFAULT_GEMINI_LIVE_MODEL,
    DEFAULT_GEMINI_LIVE_VOICE,
    DEFAULT_GEMINI_TEXT_MODEL,
    DEFAULT_INSTRUCTIONS,
    DEFAULT_MCP_URL,
    REDACTED,
    SECRET_FIELDS,
    _is_http_url,
    _validate_id,
)


class IntegrationConfig(BaseModel):
    """One Google Gemini, Google Cloud, or Home Assistant MCP integration."""

    id: str
    name: str
    kind: Literal[
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
    ]
    enabled: bool = False
    api_key: str = ""
    token: str = ""
    base_url: str = ""
    endpoint: str = ""
    language: str = ""
    speed: float = Field(default=1.0, ge=0.25, le=1.5)
    tts_streaming_mode: Literal["sentence", "token"] = "sentence"
    default_model: str = ""
    default_realtime_model: str = ""
    default_stt_model: str = ""
    default_tts_model: str = ""
    default_voice: str = ""
    project: str = ""
    location: str = ""
    provider_id: str = ""
    credentials_json: str = ""
    credentials_path: str = ""

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_id(value, "Integration id")


class PipelineStepConfig(BaseModel):
    """One visible step in the pipeline editor."""

    id: str
    kind: Literal["transport", "memory", "vad", "stt", "llm", "web_search", "tools", "flow", "tts", "output"]
    label: str
    enabled: bool = True
    integration_id: str = ""
    model: str = ""
    voice: str = ""
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_id(value, "Pipeline step id")


from .defaults import (  # noqa: E402
    default_conversation_flow,
    default_integrations,
    default_steps,
)


class FlowConfig(BaseModel):
    """One realtime assistant pipeline."""

    id: str = "home-default"
    name: str = "Gemini Live Home Assistant"
    enabled: bool = True
    mode: Literal["realtime", "composed", "classic", "text"] = "realtime"
    pipeline_template: Literal[
        "gemini_live_home",
        "gemini_composed_home",
        "custom",
    ] = "gemini_live_home"
    provider_id: str = "gemini"
    model: str = DEFAULT_GEMINI_LIVE_MODEL
    text_model: str = DEFAULT_GEMINI_TEXT_MODEL
    voice: str = DEFAULT_GEMINI_LIVE_VOICE
    speed: float = Field(default=1.0, ge=0.25, le=1.5)
    language: str | None = ""
    instructions: str = DEFAULT_INSTRUCTIONS
    greeting: str = ""
    transcription_model: str = "gemini-live"
    noise_reduction: Literal["off", "near_field", "far_field"] = "near_field"
    vad_mode: Literal["semantic_vad", "server_vad"] = "semantic_vad"
    vad_eagerness: Literal["low", "medium", "high", "auto"] = "low"
    interrupt_response: bool = False
    max_output_tokens: int | None = Field(default=None, ge=1, le=4096)
    reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None
    mcp_enabled: bool = True
    mcp_tool_allowlist: list[str] = Field(default_factory=list)
    memory_enabled: bool = True
    web_search_enabled: bool = False
    video_enabled: bool = False
    steps: list[PipelineStepConfig] = Field(default_factory=default_steps)
    conversation_flow: dict[str, Any] = Field(default_factory=default_conversation_flow)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_id(value, "Flow id")

    def model_step(self) -> PipelineStepConfig | None:
        """Return the primary LLM/realtime step."""

        return next((step for step in self.steps if step.kind == "llm" and step.enabled), None)


class RuntimeConfig(BaseModel):
    """Persisted runtime configuration edited by the web UI."""

    version: int = 22
    google_api_key: str = ""
    text_model: str = DEFAULT_GEMINI_TEXT_MODEL
    ha_mcp_url: str = ""
    longlived_token: str = ""
    satellite_shared_secret: str = ""
    runner_host: str = ""
    runner_port: int = Field(default=7865, ge=1024, le=65535)
    esphome_follow_up_ms: int = Field(default=30000, ge=0, le=60000)
    esphome_follow_up_open_delay_ms: int = Field(default=80, ge=0, le=5000)
    esphome_wake_open_delay_ms: int = Field(default=0, ge=0, le=5000)
    esphome_playback_prebuffer_ms: int = Field(default=300, ge=0, le=2000)
    esp32_mode: bool = False
    enable_default_ice_servers: bool = False
    audio_debug_enabled: bool = False
    audio_debug_keep_sessions: int = Field(default=10, ge=1, le=100)
    session_memory_enabled: bool = True
    session_memory_reuse_seconds: int = Field(default=300, ge=0, le=86400)
    session_memory_max_messages: int = Field(default=12, ge=0, le=100)
    mcp_tools_cache_enabled: bool = True
    mcp_tools_cache_ttl_seconds: int = Field(default=300, ge=0, le=86400)
    ai_image_provider_id: str = ""
    selected_flow_id: str = "home-default"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    integrations: list[IntegrationConfig] = Field(default_factory=default_integrations)
    flows: list[FlowConfig] = Field(default_factory=lambda: [FlowConfig()])

    @field_validator("flows")
    @classmethod
    def validate_flows(cls, value: list[FlowConfig]) -> list[FlowConfig]:
        if not value:
            raise ValueError("At least one flow is required")
        ids = [flow.id for flow in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Flow ids must be unique")
        return value

    @field_validator("integrations")
    @classmethod
    def validate_integrations(cls, value: list[IntegrationConfig]) -> list[IntegrationConfig]:
        if not value:
            raise ValueError("At least one integration is required")
        ids = [integration.id for integration in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Integration ids must be unique")
        return value

    def selected_flow(self, requested_flow_id: str | None = None) -> FlowConfig:
        """Return the requested flow, falling back to selected/default flow."""

        candidates = [requested_flow_id, self.selected_flow_id, self.flows[0].id]
        for candidate in candidates:
            if not candidate:
                continue
            for flow in self.flows:
                if flow.id == candidate:
                    return flow
        return self.flows[0]

    def integration(self, integration_id: str | None) -> IntegrationConfig | None:
        """Return an integration by id."""

        if not integration_id:
            return None
        return next((item for item in self.integrations if item.id == integration_id), None)

    def model_integration(self, flow: FlowConfig) -> IntegrationConfig | None:
        """Return the integration used by the primary model step."""

        step = flow.model_step()
        integration_id = step.integration_id if step and step.integration_id else flow.provider_id
        return self.integration(integration_id)

    @property
    def mcp_integration(self) -> IntegrationConfig | None:
        """Return the Home Assistant MCP integration."""

        return next(
            (
                item
                for item in self.integrations
                if item.kind == "home_assistant_mcp" and item.id == "ha-mcp"
            ),
            None,
        ) or next(
            (item for item in self.integrations if item.kind == "home_assistant_mcp"),
            None,
        )

    @property
    def mcp_integrations(self) -> list[IntegrationConfig]:
        """Return enabled MCP server integrations."""

        return [
            item
            for item in self.integrations
            if item.enabled and item.kind in {"home_assistant_mcp", "ha_mcp", "mcp_server"}
        ]

    @property
    def effective_mcp_url(self) -> str:
        """Return the MCP URL used by the add-on."""

        integration = self.mcp_integration
        for candidate in (
            integration.base_url if integration else "",
            self.ha_mcp_url,
            os.getenv("HA_MCP_URL"),
            DEFAULT_MCP_URL,
        ):
            if _is_http_url(candidate):
                return candidate.strip()
        return DEFAULT_MCP_URL

    @property
    def effective_mcp_token(self) -> str:
        """Return the Home Assistant token used for MCP."""

        integration = self.mcp_integration
        return (
            (integration.token if integration else "")
            or self.longlived_token
            or os.getenv("LONGLIVED_TOKEN")
            or os.getenv("SUPERVISOR_TOKEN", "")
        )

    @property
    def effective_mcp_token_source(self) -> str:
        """Return where the effective MCP token came from."""

        integration = self.mcp_integration
        if integration and integration.token:
            return "integration"
        if self.longlived_token or os.getenv("LONGLIVED_TOKEN"):
            return "long-lived"
        if os.getenv("SUPERVISOR_TOKEN"):
            return "supervisor"
        return ""

    def enabled_mcp_servers(self, token_override: str = "") -> list[dict[str, Any]]:
        """Return enabled MCP server connection specs."""

        servers: list[dict[str, Any]] = []
        for integration in self.mcp_integrations:
            if integration.kind == "home_assistant_mcp":
                url = self.effective_mcp_url
                token = token_override or self.effective_mcp_token
                if not _is_http_url(url):
                    continue
                servers.append(
                    {
                        "id": integration.id,
                        "name": integration.name,
                        "url": url,
                        "token": token,
                        "prefer_unprefixed": True,
                    }
                )
                continue

            url = (integration.base_url or integration.endpoint or "").strip()
            if not _is_http_url(url):
                continue
            servers.append(
                {
                    "id": integration.id,
                    "name": integration.name,
                    "url": url,
                    "token": (integration.token or integration.api_key or "").strip(),
                    "prefer_unprefixed": False,
                }
            )
        return servers

    def public_dict(self) -> dict[str, Any]:
        """Return configuration safe enough for the UI."""

        data = self.model_dump()
        for key in ("google_api_key", "longlived_token", "satellite_shared_secret"):
            configured = bool(data.get(key))
            data[f"{key}_configured"] = configured
            data[key] = REDACTED if configured else ""

        for integration in data["integrations"]:
            for key in SECRET_FIELDS:
                configured = bool(integration.get(key))
                integration[f"{key}_configured"] = configured
                integration[key] = REDACTED if configured else ""

        data["effective_mcp_url"] = self.effective_mcp_url
        data["mcp_token_source"] = self.effective_mcp_token_source
        return data
