"""Session, memory, project, and artifact API endpoints."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any
import uuid

from aiohttp import web

from libhippo.runner.harness import GeneralAgentHarness
from libhippo.runner.persistence import ConversationSession, context_messages_to_chat_messages
import shutil


def setup_session_routes(app: web.Application, harness: GeneralAgentHarness) -> None:
    """Register session and context inspection routes."""

    async def get_session_info(request: web.Request) -> web.Response:
        msgs = [asdict(m) for m in harness.memory.get_all_messages()]
        chat_msgs = context_messages_to_chat_messages(harness.memory.zone2_history)
        return web.json_response({
            "project_id": harness.project_manager.project_id,
            "conversation_id": harness.session.conversation_id,
            "conversation_name": harness.session.get_name(),
            "conversation_metadata": harness.session.get_metadata(),
            "is_running": getattr(harness, "is_running", False),
            "current_turns": harness.governor.current_turns,
            "max_turns": harness.config.max_turns,
            "total_tokens": harness.memory.get_total_tokens(),
            "soft_watermark": harness.config.soft_token_watermark,
            "hard_limit": harness.config.hard_token_limit,
            "compaction_target": harness.config.compaction_target_tokens,
            "is_paused": harness._is_paused,
            "messages": msgs,
            "chat_messages": chat_msgs,
        })

    async def compact_session(request: web.Request) -> web.Response:
        evicted = harness.compact_context()
        return web.json_response({
            "status": "ok",
            "evicted_tokens": evicted,
            "total_tokens": harness.memory.get_total_tokens(),
        })

    async def reset_session(request: web.Request) -> web.Response:
        name = None
        try:
            if request.can_read_body:
                body = await request.json()
                if isinstance(body, dict):
                    name = body.get("name")
        except Exception:
            pass
        new_conv_id = harness.new_conversation(name=name)
        return web.json_response({
            "status": "ok",
            "conversation_id": new_conv_id,
            "name": harness.session.get_name(),
            "metadata": harness.session.get_metadata(),
        })

    async def list_projects(request: web.Request) -> web.Response:
        projects_dir = harness.config.user_config_dir / "projects"
        projects: list[dict[str, Any]] = []

        if projects_dir.is_dir():
            for p in projects_dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    pid = data.get("project_id", p.stem)
                    conv_dir = projects_dir / pid / "conversations"
                    conv_count = len(list(conv_dir.iterdir())) if conv_dir.is_dir() else 0
                    projects.append({
                        "project_id": pid,
                        "workspace_root": data.get("workspace_root", ""),
                        "conversations_count": conv_count,
                        "is_active": pid == harness.project_manager.project_id,
                    })
                except Exception:
                    pass

        return web.json_response({"projects": projects})

    async def get_conversation(request: web.Request) -> web.Response:
        conv_id = request.match_info["conv_id"]
        sess = ConversationSession(
            project_id=harness.project_manager.project_id,
            conversation_id=conv_id,
            config=harness.config,
        )
        context_msgs = sess.get_messages()
        msgs = [asdict(m) for m in context_msgs]
        chat_msgs = context_messages_to_chat_messages(context_msgs)
        artifacts = sess.list_artifacts()
        subagents = sess.list_subagents()

        return web.json_response({
            "conversation_id": conv_id,
            "name": sess.get_name(),
            "metadata": sess.get_metadata(),
            "messages": msgs,
            "chat_messages": chat_msgs,
            "artifacts": artifacts,
            "subagents": subagents,
            "is_active": (conv_id == harness.session.conversation_id),
        })

    async def list_conversations(request: web.Request) -> web.Response:
        convs = harness.list_conversations()
        return web.json_response({
            "conversations": convs,
            "active_conversation_id": harness.session.conversation_id,
        })

    async def load_conversation(request: web.Request) -> web.Response:
        conv_id = request.match_info["conv_id"]
        harness.load_conversation(conv_id)
        chat_msgs = context_messages_to_chat_messages(harness.memory.zone2_history)
        return web.json_response({
            "status": "ok",
            "conversation_id": conv_id,
            "name": harness.session.get_name(),
            "metadata": harness.session.get_metadata(),
            "chat_messages": chat_msgs,
            "total_tokens": harness.memory.get_total_tokens(),
        })

    async def update_conversation(request: web.Request) -> web.Response:
        conv_id = request.match_info["conv_id"]
        sess = ConversationSession(
            project_id=harness.project_manager.project_id,
            conversation_id=conv_id,
            config=harness.config,
        )
        try:
            body = await request.json()
            if isinstance(body, dict) and "name" in body:
                sess.set_name(body["name"])
                if harness.session.conversation_id == conv_id:
                    harness.session.set_name(body["name"])
        except Exception:
            pass
        return web.json_response({
            "status": "ok",
            "conversation_id": conv_id,
            "name": sess.get_name(),
            "metadata": sess.get_metadata(),
        })

    async def delete_conversation(request: web.Request) -> web.Response:
        conv_id = request.match_info["conv_id"]
        conv_dir = harness.config.get_conversations_dir() / conv_id
        if conv_dir.is_dir():
            shutil.rmtree(conv_dir, ignore_errors=True)
        if harness.session.conversation_id == conv_id:
            harness.new_conversation()
        return web.json_response({"status": "ok", "deleted": conv_id})

    async def list_artifacts(request: web.Request) -> web.Response:
        artifacts = harness.session.list_artifacts()
        return web.json_response({"artifacts": artifacts})

    async def get_artifact(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        clean_name = name if name.endswith(".md") else f"{name}.md"
        art_path = harness.session.artifacts_dir / clean_name
        if not art_path.is_file():
            return web.json_response({"error": "Artifact not found"}, status=404)

        meta_path = harness.session.artifacts_dir / f"{clean_name}.meta.json"
        meta: dict[str, Any] = {}
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        return web.json_response({
            "name": clean_name,
            "content": art_path.read_text(encoding="utf-8"),
            "metadata": meta,
        })

    async def undo_message(request: web.Request) -> web.Response:
        data = await request.json()
        message_id = data.get("message_id", "")
        res = await harness.undo(message_id)
        return web.json_response(res)

    async def stop_knowledge(request: web.Request) -> web.Response:
        count = harness.stop_knowledge_workers()
        return web.json_response({"status": "ok", "stopped_workers": count})

    app.router.add_get("/api/session", get_session_info)
    app.router.add_post("/api/session/compact", compact_session)
    app.router.add_post("/api/session/reset", reset_session)
    app.router.add_post("/api/session/undo", undo_message)
    app.router.add_post("/api/knowledge/stop", stop_knowledge)
    app.router.add_get("/api/projects", list_projects)
    app.router.add_get("/api/conversations", list_conversations)
    app.router.add_get("/api/conversations/{conv_id}", get_conversation)
    app.router.add_patch("/api/conversations/{conv_id}", update_conversation)
    app.router.add_post("/api/conversations/{conv_id}/load", load_conversation)
    app.router.add_delete("/api/conversations/{conv_id}", delete_conversation)
    app.router.add_get("/api/artifacts", list_artifacts)
    app.router.add_get("/api/artifacts/{name}", get_artifact)
