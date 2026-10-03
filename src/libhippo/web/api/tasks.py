"""Sandbox background tasks and subagents API endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiohttp import web

from libhippo.runner.harness import GeneralAgentHarness


def setup_tasks_routes(app: web.Application, harness: GeneralAgentHarness) -> None:
    """Register background tasks and subagents management routes."""

    async def list_tasks(request: web.Request) -> web.Response:
        tasks: list[dict[str, Any]] = []

        # 1. Active in-memory tasks
        if hasattr(harness.sandbox, "active_tasks"):
            for tid, info in harness.sandbox.active_tasks.items():
                tasks.append({
                    "task_id": tid,
                    "command": info.command,
                    "cwd": str(info.cwd),
                    "pid": info.pid,
                    "status": "running" if info.is_running() else "completed",
                    "exit_code": info.exit_code,
                    "log_path": str(info.log_path),
                    "start_time": info.start_time,
                })

        # 2. Historical tasks from tasks_dir
        tasks_dir = getattr(harness.sandbox, "tasks_dir", harness.workspace_root / "tasks")
        if tasks_dir.is_dir():
            for p in tasks_dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    # If not already in active tasks
                    if not any(t["task_id"] == data.get("task_id") for t in tasks):
                        tasks.append(data)
                except Exception:
                    pass

        return web.json_response({"tasks": tasks})

    async def manage_task(request: web.Request) -> web.Response:
        task_id = request.match_info["task_id"]
        data = await request.json()
        action = data.get("action", "status")
        input_data = data.get("input")

        if not hasattr(harness.sandbox, "manage_task"):
            return web.json_response({"error": "Sandbox does not support task management"}, status=400)

        result = await harness.sandbox.manage_task(action=action, task_id=task_id, input=input_data)
        return web.json_response({"task_id": task_id, "result": result})

    async def get_task_log(request: web.Request) -> web.Response:
        task_id = request.match_info["task_id"]
        tasks_dir = getattr(harness.sandbox, "tasks_dir", harness.workspace_root / "tasks")
        log_file = tasks_dir / f"{task_id}.log"

        if not log_file.is_file():
            return web.json_response({"error": "Log file not found"}, status=404)

        return web.json_response({
            "task_id": task_id,
            "log": log_file.read_text(encoding="utf-8", errors="replace"),
        })

    async def list_subagents(request: web.Request) -> web.Response:
        subs: list[dict[str, Any]] = []

        if hasattr(harness, "subagents") and harness.subagents:
            for sid, rec in getattr(harness.subagents, "active_subagents", {}).items():
                subs.append({
                    "subagent_id": sid,
                    "role": rec.role,
                    "prompt": rec.prompt,
                    "state": rec.state,
                    "model": rec.model,
                    "start_time": rec.start_time,
                })

        # Also merge persisted from session
        persisted = harness.session.list_subagents()
        for p in persisted:
            if not any(s["subagent_id"] == p.get("subagent_id") for s in subs):
                subs.append(p)

        return web.json_response({"subagents": subs})

    async def message_subagent(request: web.Request) -> web.Response:
        subagent_id = request.match_info["subagent_id"]
        data = await request.json()
        message = data.get("message", "")

        if not hasattr(harness, "subagents") or not harness.subagents:
            return web.json_response({"error": "Subagent manager not initialized"}, status=400)

        res = await harness.subagents.send_message(recipient=subagent_id, message=message)
        return web.json_response({"status": "ok", "response": res})

    app.router.add_get("/api/tasks", list_tasks)
    app.router.add_post("/api/tasks/{task_id}", manage_task)
    app.router.add_get("/api/tasks/{task_id}/log", get_task_log)
    app.router.add_get("/api/subagents", list_subagents)
    app.router.add_post("/api/subagents/{subagent_id}/message", message_subagent)
