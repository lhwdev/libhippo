"""aiohttp.web server for LibHippo General Agent Harness."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from libhippo.runner.harness import GeneralAgentHarness
from libhippo.runner.persistence import context_messages_to_chat_messages
from libhippo.web.api import (
    setup_autocomplete_routes,
    setup_knowledge_routes,
    setup_session_routes,
    setup_settings_routes,
    setup_tasks_routes,
)

logger = logging.getLogger(__name__)


@web.middleware
async def cors_middleware(request: web.Request, handler: Any) -> web.Response:
    """Allow CORS for local dev server (e.g. Vite on 5173)."""
    if request.method == "OPTIONS":
        response = web.Response()
    else:
        try:
            response = await handler(request)
        except web.HTTPException as ex:
            response = ex

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    return response


HARNESS_KEY = web.AppKey("harness", GeneralAgentHarness)
WEBSOCKETS_KEY = web.AppKey("websockets", set)


def create_app(
    harness: GeneralAgentHarness | None = None,
    static_dir: Path | None = None,
) -> web.Application:
    """Create and configure the aiohttp web application."""
    if harness is None:
        harness = GeneralAgentHarness()

    app = web.Application(middlewares=[cors_middleware])
    app[HARNESS_KEY] = harness
    active_websockets: set[web.WebSocketResponse] = set()
    app[WEBSOCKETS_KEY] = active_websockets

    # Register tool event callback to broadcast interactive events (approvals, questions)
    async def broadcast_tool_event(event_data: dict[str, Any]) -> None:
        msg = json.dumps(event_data)
        coros = [ws.send_str(msg) for ws in list(active_websockets) if not ws.closed]
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)

    if hasattr(harness, "tools") and harness.tools:
        harness.tools.event_callback = broadcast_tool_event

    async def broadcast_worker_status() -> None:
        msg = json.dumps({
            "type": "knowledge_worker_status",
            "active": harness.has_active_knowledge_workers,
            "has_active_knowledge_workers": harness.has_active_knowledge_workers,
            "count": len([t for t in harness._active_sidecar_tasks if not t.done()]),
        })
        coros = [ws.send_str(msg) for ws in list(active_websockets) if not ws.closed]
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)

    async def broadcast_knowledge_event(event: Any) -> None:
        event_dict = asdict(event) if is_dataclass(event) else event
        msg = json.dumps(event_dict)
        coros = [ws.send_str(msg) for ws in list(active_websockets) if not ws.closed]
        if coros:
            await asyncio.gather(*coros, return_exceptions=True)
        await broadcast_worker_status()

    if hasattr(harness, "on_event_broadcast"):
        harness.on_event_broadcast = broadcast_knowledge_event

    # 1. Register API Routes
    setup_settings_routes(app, harness)
    setup_autocomplete_routes(app, harness)
    setup_session_routes(app, harness)
    setup_tasks_routes(app, harness)
    setup_knowledge_routes(app, harness)

    # 2. WebSocket Handler
    async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)
        active_websockets.add(ws)

        try:
            # Send initial greeting and session state
            init_state = {
                "type": "connection_established",
                "project_id": harness.project_manager.project_id,
                "conversation_id": harness.session.conversation_id,
                "conversation_name": harness.session.get_name(),
                "conversation_metadata": harness.session.get_metadata(),
                "is_running": harness.is_running,
                "total_tokens": harness.memory.get_total_tokens(),
                "reasoning_effort": harness.config.model.reasoning_effort or "medium",
                "has_active_knowledge_workers": harness.has_active_knowledge_workers,
                "chat_messages": context_messages_to_chat_messages(harness.memory.zone2_history),
            }
            await ws.send_json(init_state)

            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        payload = json.loads(msg.data)
                    except Exception as e:
                        await ws.send_json({"type": "error", "message": f"Invalid JSON: {e}"})
                        continue

                    msg_type = payload.get("type")

                    if msg_type == "prompt":
                        content = payload.get("content", "")
                        continue_mode = bool(payload.get("continue_mode", False))
                        if getattr(harness, "is_running", False):
                            result = await harness.steer(content)
                            res_clean: Any = str(result)
                            if isinstance(result, dict):
                                res_clean = {
                                    k: v if isinstance(v, (str, int, float, bool, list, dict, type(None))) else str(v)
                                    for k, v in result.items()
                                }
                            if not ws.closed:
                                await ws.send_json({"type": "steer_result", "guidance": content, "result": res_clean})
                        else:
                            # Stream turn events to client
                            async for event in harness.stream(content, continue_mode=continue_mode):
                                event_dict = asdict(event) if is_dataclass(event) else event
                                if not ws.closed:
                                    await ws.send_json(event_dict)

                    elif msg_type == "continue":
                        content = payload.get("content") or "Continue working on the previous task."
                        if getattr(harness, "is_running", False):
                            result = await harness.steer(content)
                            res_clean: Any = str(result)
                            if isinstance(result, dict):
                                res_clean = {
                                    k: v if isinstance(v, (str, int, float, bool, list, dict, type(None))) else str(v)
                                    for k, v in result.items()
                                }
                            if not ws.closed:
                                await ws.send_json({"type": "steer_result", "guidance": content, "result": res_clean})
                        else:
                            async for event in harness.stream(content, continue_mode=True):
                                event_dict = asdict(event) if is_dataclass(event) else event
                                if not ws.closed:
                                    await ws.send_json(event_dict)

                    elif msg_type == "steer":
                        guidance = payload.get("guidance", "")
                        result = await harness.steer(guidance)
                        res_clean: Any = str(result)
                        if isinstance(result, dict):
                            res_clean = {
                                k: v if isinstance(v, (str, int, float, bool, list, dict, type(None))) else str(v)
                                for k, v in result.items()
                            }
                        await ws.send_json({"type": "steer_result", "guidance": guidance, "result": res_clean})

                    elif msg_type == "interrupt":
                        reason = payload.get("reason", "paused_by_user")
                        steer_text = payload.get("steer_text")
                        harness.interrupt(reason=reason, steer_text=steer_text)
                        await ws.send_json({"type": "interrupt_result", "status": "paused_by_user"})

                    elif msg_type == "sidecar":
                        query = payload.get("query", "")
                        try:
                            sidecar_res = await harness.ask_sidecar(query)
                            await ws.send_json({
                                "type": "sidecar_response",
                                "query": query,
                                "response": sidecar_res,
                            })
                        except Exception as ex:
                            await ws.send_json({
                                "type": "sidecar_error",
                                "query": query,
                                "error": str(ex),
                            })

                    elif msg_type == "approval_response":
                        req_id = payload.get("request_id")
                        approved = payload.get("approved", False)
                        if hasattr(harness, "tools") and req_id:
                            harness.tools._interactive_responses[req_id] = approved
                        await ws.send_json({"type": "approval_acknowledged", "request_id": req_id, "approved": approved})

                    elif msg_type == "modal_answer":
                        q_id = payload.get("question_id")
                        answers = payload.get("answers", [])
                        if hasattr(harness, "tools") and q_id:
                            harness.tools._interactive_responses[q_id] = {
                                "status": "answered",
                                "answers": answers,
                            }
                        await ws.send_json({"type": "modal_acknowledged", "question_id": q_id})

                    elif msg_type == "compact":
                        evicted = harness.compact_context()
                        await ws.send_json({
                            "type": "compact_result",
                            "evicted_tokens": evicted,
                            "total_tokens": harness.memory.get_total_tokens(),
                        })

                    elif msg_type == "undo":
                        msg_id = payload.get("message_id")
                        undo_res = await harness.undo(msg_id)
                        await ws.send_json({"type": "undo_result", **undo_res})
                        await broadcast_worker_status()
                        sync_msg = json.dumps({
                            "type": "session_updated",
                            "chat_messages": undo_res.get("chat_messages", []),
                            "total_tokens": undo_res.get("total_tokens", 0),
                        })
                        for other_ws in list(active_websockets):
                            if not other_ws.closed:
                                try:
                                    await other_ws.send_str(sync_msg)
                                except Exception:
                                    pass

                    elif msg_type == "stop_knowledge":
                        stopped = harness.stop_knowledge_workers()
                        await asyncio.sleep(0)
                        await ws.send_json({"type": "stop_knowledge_result", "stopped": stopped})
                        await broadcast_worker_status()

                    elif msg_type == "set_reasoning_effort":
                        effort = payload.get("effort", "medium")
                        harness.set_reasoning_effort(effort)
                        if hasattr(harness.model_client, "update_configuration"):
                            try:
                                await harness.model_client.update_configuration(effort)
                            except Exception:
                                pass
                        await ws.send_json({
                            "type": "reasoning_effort_updated",
                            "effort": effort,
                            "reasoning_effort": effort,
                        })

                elif msg.type == WSMsgType.ERROR:
                    logger.error("WebSocket connection closed with exception: %s", ws.exception())

        finally:
            active_websockets.discard(ws)

        return ws

    app.router.add_get("/ws/events", websocket_handler)

    # 3. Static SPA Serving
    dist_dir = static_dir or (Path(__file__).parent.parent.parent.parent / "frontend" / "web" / "dist")
    index_file = dist_dir / "index.html"

    if dist_dir.is_dir() and index_file.is_file():
        # Serve static assets
        if (dist_dir / "assets").is_dir():
            app.router.add_static("/assets/", path=str(dist_dir / "assets"), name="assets")

        async def index_handler(request: web.Request) -> web.FileResponse:
            return web.FileResponse(index_file)

        app.router.add_get("/", index_handler)

        # Catch-all for SPA client routing (excluding /api and /ws)
        async def spa_fallback(request: web.Request) -> web.Response:
            if request.path.startswith("/api/") or request.path.startswith("/ws"):
                raise web.HTTPNotFound()
            req_file = dist_dir / request.path.lstrip("/")
            if req_file.is_file():
                return web.FileResponse(req_file)
            return web.FileResponse(index_file)

        app.router.add_get("/{tail:.*}", spa_fallback)
    else:
        # Fallback page if frontend is not yet built
        async def fallback_index(request: web.Request) -> web.Response:
            html = (
                "<!DOCTYPE html><html><head><title>LibHippo Agent Harness</title>"
                "<style>body{font-family:sans-serif;background:#0f172a;color:#e2e8f0;padding:2rem;line-height:1.6;}"
                "pre{background:#1e293b;padding:1rem;border-radius:6px;overflow-x:auto;}</style></head><body>"
                "<h1>LibHippo Agent Harness API Server</h1>"
                "<p>Backend is running with WebSocket and REST endpoints live.</p>"
                "<h3>Frontend Build:</h3>"
                "<p>To build the React single-page frontend:</p>"
                "<pre>cd frontend/web\nnpm install\nnpm run build</pre>"
                "<p>Or run the Vite development server with HMR:</p>"
                "<pre>cd frontend/web\nnpm run dev</pre>"
                "</body></html>"
            )
            return web.Response(text=html, content_type="text/html")

        app.router.add_get("/", fallback_index)

    return app


def run_server(
    harness: GeneralAgentHarness | None = None,
    host: str = "127.0.0.1",
    port: int = 8080,
    static_dir: Path | None = None,
) -> None:
    """Start the LibHippo web server synchronously."""
    app = create_app(harness=harness, static_dir=static_dir)
    web.run_app(app, host=host, port=port)
