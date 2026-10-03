"""Autocomplete and introspection API endpoints for commands, files, and tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from aiohttp import web

from libhippo.runner.harness import GeneralAgentHarness


def setup_autocomplete_routes(app: web.Application, harness: GeneralAgentHarness) -> None:
    """Register autocomplete routes."""

    async def get_commands(request: web.Request) -> web.Response:
        # 1. Built-in slash commands
        builtin_commands = [
            {
                "command": "/btw",
                "name": "Sidecar Query",
                "description": "Ask sidecar question without disrupting primary agent (warm KV cache)",
                "syntax": "/btw <question>",
                "type": "builtin",
            },
            {
                "command": "/steer",
                "name": "Steer Guidance",
                "description": "Send mid-turn steering guidance to redirect execution",
                "syntax": "/steer <guidance>",
                "type": "builtin",
            },
            {
                "command": "/stop",
                "name": "Interrupt / Stop",
                "description": "Pause execution, cancel model stream, and kill active tasks",
                "syntax": "/stop",
                "type": "builtin",
            },
            {
                "command": "/compact",
                "name": "Compact Context",
                "description": "Evict older tool output payloads from Zone 3 context memory",
                "syntax": "/compact",
                "type": "builtin",
            },
            {
                "command": "/status",
                "name": "System Status",
                "description": "Inspect git branch, session duration, user info, and tokens",
                "syntax": "/status",
                "type": "builtin",
            },
            {
                "command": "/mode",
                "name": "Autonomy Mode",
                "description": "Switch operational mode (turbo, default, request_review)",
                "syntax": "/mode <turbo|default|request_review>",
                "type": "builtin",
            },
            {
                "command": "/query",
                "name": "Query Knowledge",
                "description": "Test adaptive knowledge retrieval from mounted namespaces",
                "syntax": "/query <query text>",
                "type": "builtin",
            },
            {
                "command": "/clear",
                "name": "Clear Session",
                "description": "Reset session memory and start a fresh conversational turn",
                "syntax": "/clear",
                "type": "builtin",
            },
        ]

        # 2. Discovered skills (.agents convention)
        skill_commands = [
            {
                "command": f"/{skill.name}",
                "name": skill.name,
                "description": skill.description or "Custom discovered skill",
                "syntax": f"/{skill.name}",
                "type": "skill",
                "tools": skill.tool_dependencies,
            }
            for skill in harness.skills.values()
        ]

        return web.json_response({
            "commands": builtin_commands + skill_commands,
        })

    async def get_files(request: web.Request) -> web.Response:
        query = request.query.get("q", "").strip().lower()
        ws = harness.workspace_root
        results: list[dict[str, str]] = []

        ignore_dirs = {".git", ".libhippo", "node_modules", "__pycache__", ".venv", ".pytest_cache", "dist"}

        for root, dirs, files in os.walk(ws):
            dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]
            for f in files:
                if f.startswith("."):
                    continue
                full_path = Path(root) / f
                try:
                    rel_path = str(full_path.relative_to(ws)).replace("\\", "/")
                except ValueError:
                    continue

                if not query or query in rel_path.lower():
                    results.append({"path": rel_path, "name": f})
                    if len(results) >= 30:
                        break
            if len(results) >= 30:
                break

        return web.json_response({"files": results})

    async def get_tools(request: web.Request) -> web.Response:
        tools_list = [
            {
                "name": t.name,
                "description": t.description,
                "parameters_schema": t.parameters_schema,
                "requires_approval": t.requires_approval,
                "requires_sandbox_bypass": t.requires_sandbox_bypass,
            }
            for t in harness.registered_tools.values()
        ]
        return web.json_response({"tools": tools_list})

    app.router.add_get("/api/autocomplete/commands", get_commands)
    app.router.add_get("/api/autocomplete/files", get_files)
    app.router.add_get("/api/autocomplete/tools", get_tools)
