"""Pipecat Flows node configuration builders."""

from __future__ import annotations

from typing import Any

from app.config import FlowConfig
from app.mcp_bridge import CombinedMCPBridge
from app.pipeline.builder import _effective_instructions


def _flow_enabled(flow: FlowConfig) -> bool:
    return bool(flow.conversation_flow.get("enabled"))


def _flow_node_configs(flow: FlowConfig, bridge: CombinedMCPBridge | None):
    from pipecat_flows import FlowsFunctionSchema

    nodes = flow.conversation_flow.get("nodes") or []
    by_id: dict[str, dict[str, Any]] = {}

    def _messages_text(value: Any, fallback: str = "") -> str:
        if isinstance(value, str):
            return value or fallback
        if isinstance(value, list):
            parts = [
                str(item.get("content", ""))
                for item in value
                if isinstance(item, dict) and item.get("content")
            ]
            return "\n".join(parts) or fallback
        return fallback

    def node_config(node: dict[str, Any]) -> dict[str, Any]:
        node_id = str(node.get("id") or node.get("label") or "node")
        if node_id in by_id:
            return by_id[node_id]
        data = node.get("data") if isinstance(node.get("data"), dict) else node

        functions = []
        for fn in data.get("functions") or []:
            name = str(fn.get("name") or f"go_to_{fn.get('next_node_id', 'next')}")
            description = str(fn.get("description") or "Continue the conversation.")
            properties = fn.get("properties") if isinstance(fn.get("properties"), dict) else {}
            required = fn.get("required") if isinstance(fn.get("required"), list) else []
            decision = fn.get("decision") if isinstance(fn.get("decision"), dict) else {}
            next_node_id = str(
                fn.get("next_node_id")
                or decision.get("default_next_node_id")
                or ""
            )
            mcp_tool = str(fn.get("mcp_tool") or "")

            async def handler(args, flow_manager, *, next_node_id=next_node_id, mcp_tool=mcp_tool):
                result: Any = {"status": "ok"}
                if mcp_tool:
                    if not bridge:
                        result = {"status": "error", "error": "Home Assistant MCP is not connected"}
                    else:
                        result = {
                            "status": "ok",
                            "tool": mcp_tool,
                            "result": await bridge.call_tool(mcp_tool, dict(args or {})),
                        }
                next_node = by_id.get(next_node_id) if next_node_id else None
                return result, next_node

            functions.append(
                FlowsFunctionSchema(
                    name=name,
                    description=description,
                    properties=properties,
                    required=required,
                    handler=handler,
                )
            )

        config_node: dict[str, Any] = {
            "name": node_id,
            "role_message": _messages_text(data.get("role_messages"), str(data.get("role_message") or _effective_instructions(flow))),
            "task_messages": data.get("task_messages") if isinstance(data.get("task_messages"), list) else [
                {
                    "role": "developer",
                    "content": str(data.get("task") or "Continue the conversation."),
                }
            ],
            "functions": functions,
            "respond_immediately": bool(data.get("respond_immediately", True)),
        }
        if data.get("pre_actions"):
            config_node["pre_actions"] = data.get("pre_actions")
        if data.get("post_actions"):
            config_node["post_actions"] = data.get("post_actions")
        if data.get("context_strategy"):
            config_node["context_strategy"] = data.get("context_strategy")
        by_id[node_id] = config_node
        return config_node

    for node in nodes:
        node_config(node)

    initial_id = str(flow.conversation_flow.get("initial_node_id") or "")
    if not initial_id:
        initial_id = next(
            (
                str(node.get("id"))
                for node in nodes
                if node.get("type") == "initial"
                or (isinstance(node.get("data"), dict) and node["data"].get("role_messages"))
            ),
            "",
        )
    return by_id.get(initial_id) or next(iter(by_id.values()), None)
