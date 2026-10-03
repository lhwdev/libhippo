"""Session, memory, project, and artifact API endpoints."""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any
import uuid

from aiohttp import web

from libhippo.runner.harness import GeneralAgentHarness
from libhippo.runner.persistence import ConversationSession


def setup_session_routes(app: web.Application, harness: GeneralAgentHarness) -> None:
    """Register session and context inspection routes."""

    async def get_session_info(request: web.Request) -> web.Response:
        msgs = [asdict(m) for m in harness.memory.get_all_messages()]
        return web.json_response({
            "project_id": harness.project_manager.project_id,
            "conversation_id": harness.session.conversation_id,
            "current_phase": harness.current_phase,
            "current_turns": harness.governor.current_turns,
            "max_turns": harness.config.max_turns,
            "total_tokens": harness.memory.get_total_tokens(),
            "soft_watermark": harness.config.soft_token_watermark,
            "hard_limit": harness.config.hard_token_limit,
            "compaction_target": harness.config.compaction_target_tokens,
            "is_paused": harness._is_paused,
            "messages": msgs,
        })

    async def compact_session(request: web.Request) -> web.Response:
        evicted = harness.compact_context()
        return web.json_response({
            "status": "ok",
            "evicted_tokens": evicted,
            "total_tokens": harness.memory.get_total_tokens(),
        })

    async def reset_session(request: web.Request) -> web.Response:
        new_conv_id = f"conv-{uuid.uuid4().hex[:8]}"
        harness.session = ConversationSession(
            project_id=harness.project_manager.project_id,
            conversation_id=new_conv_id,
            config=harness.config,
        )
        harness.memory.clear()
        harness.init_prefix()
        harness.governor.reset()
        harness.current_phase = "alignment"
        harness._is_paused = False
        harness._interrupt_event.clear()

        return web.json_response({
            "status": "ok",
            "conversation_id": new_conv_id,
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
        msgs = [asdict(m) for m in sess.get_messages()]
        artifacts = sess.list_artifacts()
        subagents = sess.list_subagents()

        return web.json_response({
            "conversation_id": conv_id,
            "messages": msgs,
            "artifacts": artifacts,
            "subagents": subagents,
        })

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

    app.router.add_get("/api/session", get_session_info)
    app.router.add_post("/api/session/compact", compact_session)
    app.router.add_post("/api/session/reset", reset_session)
    app.router.add_get("/api/projects", list_projects)
    app.router.add_get("/api/conversations/{conv_id}", get_conversation)
    app.router.add_get("/api/artifacts", list_artifacts)
    app.router.add_get("/api/artifacts/{name}", get_artifact)
