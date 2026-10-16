"""Text bridge used by the Home Assistant Conversation integration, focused on Gemini."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import suppress
from typing import Any

import httpx
from loguru import logger

from app.config import (
    DEFAULT_GEMINI_TEXT_MODEL,
    RuntimeConfig,
)
from app.mcp_bridge import CombinedMCPBridge
from app.web_search_tool import WEB_SEARCH_TOOL_NAME, run_gemini_web_search


def _format_gemini_function_declarations(tools_schema) -> list[dict[str, Any]]:
    """Convert Pipecat FunctionSchema objects to Gemini function declarations."""

    declarations: list[dict[str, Any]] = []
    for tool in tools_schema.standard_tools:
        declarations.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": {
                    "type": "OBJECT",
                    "properties": tool.properties or {},
                    "required": tool.required or [],
                },
            }
        )
    return declarations


def _text_model(config: RuntimeConfig, flow) -> str:
    step = flow.model_step()
    model = (getattr(step, "model", "") or flow.text_model or config.text_model or DEFAULT_GEMINI_TEXT_MODEL).strip()
    return model.removeprefix("models/")


def _web_search_step(flow):
    return next((step for step in flow.steps if step.kind == "web_search" and step.enabled), None)


def _web_search_enabled(flow) -> bool:
    return bool(flow.web_search_enabled or _web_search_step(flow))


def _effective_instructions(flow) -> str:
    return flow.instructions


async def run_text_conversation(
    config: RuntimeConfig,
    *,
    text: str,
    language: str | None,
    conversation_id: str | None,
    flow_id: str | None = None,
    mcp_token: str = "",
) -> dict[str, Any]:
    """Run a text request through Gemini with Home Assistant MCP tools."""

    flow = config.selected_flow(flow_id)
    integration = config.model_integration(flow)
    api_key = (
        (integration.api_key if integration else "")
        or config.google_api_key
        or os.getenv("GOOGLE_API_KEY", "")
    ).strip()

    if not api_key:
        return {
            "speech": "Pipecat Assist requires a Google Gemini API key to process requests.",
            "conversation_id": conversation_id,
            "continue_conversation": False,
            "error": "missing_gemini_api_key",
        }

    model = _text_model(config, flow)
    system = (
        f"{_effective_instructions(flow)}\n\n"
        "You are answering through Home Assistant Conversation text mode. "
        "Use MCP tools silently for explicit smart-home requests. "
        "Keep the final answer short and natural."
    )
    if language and str(language).lower() != "pipecat-assist":
        system += f"\nThe user's language is {language}."

    bridge: CombinedMCPBridge | None = None
    function_declarations: list[dict[str, Any]] = []

    if _web_search_enabled(flow):
        function_declarations.append(
            {
                "name": WEB_SEARCH_TOOL_NAME,
                "description": "Search the web for fresh public information and return a concise answer.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "query": {
                            "type": "STRING",
                            "description": "The search query.",
                        },
                    },
                    "required": ["query"],
                },
            }
        )

    mcp_servers = config.enabled_mcp_servers(mcp_token) if flow.mcp_enabled else []
    if mcp_servers:
        bridge = CombinedMCPBridge(mcp_servers, flow.mcp_tool_allowlist)
        try:
            await bridge.start()
            tools_schema = await bridge.tools_schema(
                cache_enabled=config.mcp_tools_cache_enabled,
                cache_ttl_seconds=config.mcp_tools_cache_ttl_seconds,
            )
            function_declarations.extend(_format_gemini_function_declarations(tools_schema))
        except Exception as err:
            logger.warning("MCP connection failed: {}", err)
            with suppress(Exception):
                await bridge.close()
            bridge = None

    async def call_tool(name: str, arguments: dict[str, Any]) -> str:
        if name == WEB_SEARCH_TOOL_NAME:
            return await run_gemini_web_search(api_key, model, str(arguments.get("query") or text))
        if bridge is None:
            return "Home Assistant MCP is not connected."
        return await bridge.call_tool(name, arguments)

    contents: list[dict[str, Any]] = [
        {"role": "user", "parts": [{"text": text}]}
    ]

    tools_payload: list[dict[str, Any]] = []
    if function_declarations:
        tools_payload.append({"functionDeclarations": function_declarations})

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            for _ in range(6):
                payload: dict[str, Any] = {
                    "contents": contents,
                    "systemInstruction": {"parts": [{"text": system}]},
                }
                if tools_payload:
                    payload["tools"] = tools_payload

                response = await client.post(url, headers=headers, json=payload)
                if response.status_code >= 400:
                    logger.error("Gemini text conversation error {}: {}", response.status_code, response.text)
                    return {
                        "speech": f"Gemini error: {response.text}",
                        "conversation_id": conversation_id,
                        "continue_conversation": False,
                        "error": "gemini_api_error",
                    }

                data = response.json()
                candidate = (data.get("candidates") or [{}])[0]
                content = candidate.get("content") or {}
                parts = content.get("parts") or []

                # Add model response to history
                contents.append(content)

                function_calls = [part["functionCall"] for part in parts if "functionCall" in part]
                if not function_calls:
                    text_parts = [part["text"] for part in parts if "text" in part]
                    speech = " ".join(part.strip() for part in text_parts if part).strip()
                    return {
                        "speech": speech or "Done.",
                        "conversation_id": conversation_id,
                        "continue_conversation": False,
                    }

                function_response_parts: list[dict[str, Any]] = []
                for fc in function_calls:
                    fn_name = str(fc.get("name") or "")
                    args = fc.get("args") if isinstance(fc.get("args"), dict) else {}
                    result = await call_tool(fn_name, args)
                    function_response_parts.append(
                        {
                            "functionResponse": {
                                "name": fn_name,
                                "response": {"output": result},
                            }
                        }
                    )

                contents.append({"role": "function", "parts": function_response_parts})

            return {
                "speech": "The request required too many tool calls and was stopped.",
                "conversation_id": conversation_id,
                "continue_conversation": False,
                "error": "tool_loop_limit",
            }
    except Exception as err:
        logger.exception("Text conversation exception: {}", err)
        return {
            "speech": f"Conversation failed: {err}",
            "conversation_id": conversation_id,
            "continue_conversation": False,
            "error": "conversation_exception",
        }
    finally:
        if bridge:
            with suppress(Exception):
                await bridge.close()
