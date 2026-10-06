"""Knowledge subsystem API endpoints for mount exploration and query testbench."""

from __future__ import annotations

import time
from typing import Any

from aiohttp import web

from libhippo.runner.harness import GeneralAgentHarness


def setup_knowledge_routes(app: web.Application, harness: GeneralAgentHarness) -> None:
    """Register knowledge subsystem routes."""

    async def get_mounts(request: web.Request) -> web.Response:
        mounts: list[dict[str, Any]] = []

        if harness.store and hasattr(harness.store, "mounts"):
            for prefix, cfg in harness.store.mounts.items():
                mounts.append({
                    "namespace": prefix,
                    "physical_path": str(cfg.physical_path),
                    "read_only": cfg.read_only,
                })
        else:
            # Fallback to default expected layout
            mounts = [
                {
                    "namespace": "project",
                    "physical_path": str(harness.workspace_root / ".libhippo"),
                    "read_only": False,
                },
                {
                    "namespace": "user",
                    "physical_path": str(harness.config.user_config_dir / "knowledge"),
                    "read_only": False,
                },
                {
                    "namespace": "common",
                    "physical_path": "knowledge/common",
                    "read_only": False,
                },
            ]

        return web.json_response({"mounts": mounts})

    async def query_knowledge(request: web.Request) -> web.Response:
        data = await request.json()
        query = data.get("query", "")
        effort = data.get("effort", "medium")
        criticality = data.get("criticality", "preferred")

        if not query:
            return web.json_response({"error": "Query string is required"}, status=400)

        start_time = time.monotonic()
        result: Any = None

        if "query_knowledge" in harness.registered_tools:
            handler = harness.registered_tools["query_knowledge"].handler
            result = await handler(query=query, effort=effort, criticality=criticality)
        elif harness.dispatcher:
            res = await harness.dispatcher.query_knowledge(query=query, effort=effort, criticality=criticality)
            result = {
                "status": res.status,
                "path": res.path,
                "title": res.title,
                "content": res.content,
                "confidence": res.confidence,
                "effort_tier": res.effort_tier,
                "criticality": res.criticality,
                "source": res.source,
            }
        elif harness.store:
            nodes = await harness.store.search(query=query, top_k=3)
            result = {
                "status": "HIT" if nodes else "MISS",
                "retrieved_nodes": [
                    {"path": n.path, "content": n.content, "confidence": n.confidence}
                    for n in nodes
                ],
            }
        else:
            result = {
                "status": "MISS",
                "snippets": [],
                "confidence": 0.0,
                "message": "Knowledge repository contains no matching documents",
            }

        elapsed = time.monotonic() - start_time
        return web.json_response({
            "query": query,
            "effort": effort,
            "criticality": criticality,
            "duration_ms": round(elapsed * 1000, 2),
            "result": result,
        })

    async def rebuild_index(request: web.Request) -> web.Response:
        await harness.post_task_maintenance()
        return web.json_response({
            "status": "ok",
            "message": "Maintenance and index rebuild completed",
        })

    app.router.add_get("/api/knowledge/mounts", get_mounts)
    app.router.add_post("/api/knowledge/query", query_knowledge)
    app.router.add_post("/api/knowledge/rebuild", rebuild_index)
