"""Global and Project settings API endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aiohttp import web

from libhippo.runner.config import GlobalMcpConfig, ProjectSecurityPolicy
from libhippo.runner.harness import GeneralAgentHarness
from libhippo.runner.types import ExecutionMode


def setup_settings_routes(app: web.Application, harness: GeneralAgentHarness) -> None:
    """Register settings management routes."""

    async def get_global_settings(request: web.Request) -> web.Response:
        user_cfg = harness.config.user_config_dir
        global_agents_path = user_cfg / "AGENTS.md"
        global_agents = (
            global_agents_path.read_text(encoding="utf-8")
            if global_agents_path.is_file()
            else ""
        )
        mcp_cfg = harness.discovery.discover_global_mcp()

        global_skills = [
            s.model_dump(mode="json")
            for s in harness.skills.values()
            if str(s.skill_path).startswith(str(Path.home()))
        ]

        return web.json_response({
            "agents_markdown": global_agents,
            "mcp": mcp_cfg.model_dump(mode="json"),
            "skills": global_skills,
            "user_config_dir": str(user_cfg),
        })

    async def update_global_settings(request: web.Request) -> web.Response:
        data = await request.json()
        user_cfg = harness.config.user_config_dir
        user_cfg.mkdir(parents=True, exist_ok=True)

        if "agents_markdown" in data and isinstance(data["agents_markdown"], str):
            global_agents_path = user_cfg / "AGENTS.md"
            global_agents_path.write_text(data["agents_markdown"], encoding="utf-8")

        if "mcp" in data and isinstance(data["mcp"], dict):
            # Validate schema
            validated_mcp = GlobalMcpConfig.model_validate(data["mcp"])
            mcp_path = harness.config.get_mcp_config_path()
            mcp_path.parent.mkdir(parents=True, exist_ok=True)
            mcp_path.write_text(validated_mcp.model_dump_json(indent=2), encoding="utf-8")

        harness.init_prefix()
        return web.json_response({"status": "ok", "message": "Global settings saved"})

    async def get_project_settings(request: web.Request) -> web.Response:
        proj_agents_path = harness.workspace_root / "AGENTS.md"
        proj_agents = (
            proj_agents_path.read_text(encoding="utf-8")
            if proj_agents_path.is_file()
            else ""
        )

        project_skills = [
            s.model_dump(mode="json")
            for s in harness.skills.values()
            if str(s.skill_path).startswith(str(harness.workspace_root))
        ]

        policy = harness.project_manager.policy

        return web.json_response({
            "project_id": harness.project_manager.project_id,
            "workspace_root": str(harness.workspace_root),
            "security_policy": policy.model_dump(mode="json"),
            "agents_markdown": proj_agents,
            "skills": project_skills,
        })

    async def update_project_settings(request: web.Request) -> web.Response:
        data = await request.json()

        if "security_policy" in data and isinstance(data["security_policy"], dict):
            pol_dict = data["security_policy"]
            # Ensure workspace_root and project_id are preserved
            pol_dict["workspace_root"] = str(harness.workspace_root)
            pol_dict["project_id"] = harness.project_manager.project_id
            validated_pol = ProjectSecurityPolicy.model_validate(pol_dict)
            harness.project_manager.save_policy(validated_pol)
            harness.project_manager.policy = validated_pol

        if "agents_markdown" in data and isinstance(data["agents_markdown"], str):
            proj_agents_path = harness.workspace_root / "AGENTS.md"
            proj_agents_path.write_text(data["agents_markdown"], encoding="utf-8")

        harness.init_prefix()
        return web.json_response({"status": "ok", "message": "Project settings saved"})

    async def get_runtime_config(request: web.Request) -> web.Response:
        return web.json_response({
            "model": harness.config.model,
            "temperature": harness.config.temperature,
            "mode": harness.config.mode.value,
            "transport_mode": harness.config.transport_mode,
            "soft_token_watermark": harness.config.soft_token_watermark,
            "hard_token_limit": harness.config.hard_token_limit,
            "compaction_target_tokens": harness.config.compaction_target_tokens,
            "max_turns": harness.config.max_turns,
            "allow_sandbox_bypass": harness.config.allow_sandbox_bypass,
        })

    async def update_runtime_config(request: web.Request) -> web.Response:
        data = await request.json()
        if "model" in data and isinstance(data["model"], str):
            harness.config.model = data["model"]
        if "temperature" in data and isinstance(data["temperature"], (int, float)):
            harness.config.temperature = float(data["temperature"])
        if "mode" in data and isinstance(data["mode"], str):
            try:
                harness.config.mode = ExecutionMode(data["mode"])
            except ValueError:
                return web.json_response(
                    {"error": f"Invalid mode. Choose from: {[m.value for m in ExecutionMode]}"},
                    status=400,
                )
        if "allow_sandbox_bypass" in data and isinstance(data["allow_sandbox_bypass"], bool):
            harness.config.allow_sandbox_bypass = data["allow_sandbox_bypass"]

        return web.json_response({"status": "ok", "message": "Runtime configuration updated"})

    app.router.add_get("/api/settings/global", get_global_settings)
    app.router.add_put("/api/settings/global", update_global_settings)
    app.router.add_get("/api/settings/project", get_project_settings)
    app.router.add_put("/api/settings/project", update_project_settings)
    app.router.add_get("/api/settings/runtime", get_runtime_config)
    app.router.add_post("/api/settings/runtime", update_runtime_config)
