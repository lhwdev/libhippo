"""Unit and integration tests for LibHippo Web Server and REST/WebSocket APIs."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer
import pytest
from autogen_core.models import CreateResult, RequestUsage

from libhippo.runner.config import HarnessConfig
from libhippo.runner.harness import GeneralAgentHarness
from libhippo.web.server import create_app


def make_mock_client(content: str = "Test response") -> AsyncMock:
    client = AsyncMock()
    client.create.return_value = CreateResult(
        finish_reason="stop",
        content=content,
        usage=RequestUsage(prompt_tokens=20, completion_tokens=10),
        cached=False,
    )
    client.steer = AsyncMock(return_value={"status": "steered_mock"})
    client.cancel = AsyncMock(return_value={"status": "cancelled_mock"})
    return client


@pytest.fixture
def test_harness(tmp_path: Path) -> GeneralAgentHarness:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True)

    config = HarnessConfig(workspace_root=ws, user_config_dir=cfg)
    mock_client = make_mock_client()
    return GeneralAgentHarness(config=config, model_client=mock_client)


@pytest.mark.asyncio
async def test_web_settings_apis(test_harness: GeneralAgentHarness):
    """Test global, project, and runtime settings REST endpoints."""
    app = create_app(harness=test_harness)
    async with TestClient(TestServer(app)) as client:
        # 1. Global settings GET
        resp = await client.get("/api/settings/global")
        assert resp.status == 200
        data = await resp.json()
        assert "agents_markdown" in data
        assert "mcp" in data

        # 2. Global settings PUT
        put_resp = await client.put(
            "/api/settings/global",
            json={"agents_markdown": "# Custom Global Guidelines\nFollow TDD."},
        )
        assert put_resp.status == 200
        # Verify read back
        resp2 = await client.get("/api/settings/global")
        data2 = await resp2.json()
        assert "# Custom Global Guidelines" in data2["agents_markdown"]

        # 3. Project settings GET
        proj_resp = await client.get("/api/settings/project")
        assert proj_resp.status == 200
        proj_data = await proj_resp.json()
        assert proj_data["project_id"] == test_harness.project_manager.project_id
        assert "security_policy" in proj_data

        # 4. Project settings PUT (update security policy)
        pol = proj_data["security_policy"]
        pol["command"]["allow"].append("npm test*")
        pol_put = await client.put(
            "/api/settings/project",
            json={"security_policy": pol, "agents_markdown": "# Project Rules"},
        )
        assert pol_put.status == 200
        assert "npm test*" in test_harness.project_manager.policy.command.allow

        # 5. Runtime settings GET and POST
        rt_resp = await client.get("/api/settings/runtime")
        assert rt_resp.status == 200
        rt_data = await rt_resp.json()
        assert rt_data["mode"] == "default"
        assert rt_data["reasoning_effort"] == "medium"

        rt_post = await client.post(
            "/api/settings/runtime",
            json={"mode": "turbo", "reasoning_effort": "high"},
        )
        assert rt_post.status == 200
        assert test_harness.config.mode.value == "turbo"
        assert test_harness.reasoning_effort == "high"


@pytest.mark.asyncio
async def test_web_autocomplete_apis(test_harness: GeneralAgentHarness):
    """Test slash commands, file search, and tools autocomplete endpoints."""
    # Create test files in workspace
    (test_harness.workspace_root / "test_module.py").write_text("print('test')", encoding="utf-8")
    (test_harness.workspace_root / "README.md").write_text("# Readme", encoding="utf-8")

    app = create_app(harness=test_harness)
    async with TestClient(TestServer(app)) as client:
        # 1. Commands autocomplete
        cmd_resp = await client.get("/api/autocomplete/commands")
        assert cmd_resp.status == 200
        cmd_data = await cmd_resp.json()
        commands = [c["command"] for c in cmd_data["commands"]]
        assert "/btw" in commands
        assert "/continue" in commands
        assert "/stop" in commands
        assert "/compact" in commands

        # 2. File autocomplete
        file_resp = await client.get("/api/autocomplete/files?q=module")
        assert file_resp.status == 200
        file_data = await file_resp.json()
        assert any("test_module.py" in f["path"] for f in file_data["files"])

        # 3. Tools introspection
        tools_resp = await client.get("/api/autocomplete/tools")
        assert tools_resp.status == 200
        tools_data = await tools_resp.json()
        tool_names = [t["name"] for t in tools_data["tools"]]
        assert "read_file" in tool_names
        assert "run_command" in tool_names


@pytest.mark.asyncio
async def test_web_session_and_knowledge_apis(test_harness: GeneralAgentHarness):
    """Test session state, compaction, reset, and knowledge testbench endpoints."""
    # Seed context memory and durable session
    msg = test_harness.memory.append_user_turn("Hello agent")
    await test_harness.session.append_message(msg)

    app = create_app(harness=test_harness)
    async with TestClient(TestServer(app)) as client:
        # 1. Session state
        sess_resp = await client.get("/api/session")
        assert sess_resp.status == 200
        sess_data = await sess_resp.json()
        assert sess_data["is_running"] is False
        assert len(sess_data["messages"]) > 0

        # 2. Context compaction
        comp_resp = await client.post("/api/session/compact")
        assert comp_resp.status == 200
        comp_data = await comp_resp.json()
        assert "evicted_tokens" in comp_data

        # 3. Knowledge mounts and query testbench
        km_resp = await client.get("/api/knowledge/mounts")
        assert km_resp.status == 200
        km_data = await km_resp.json()
        assert len(km_data["mounts"]) > 0

        kq_resp = await client.post(
            "/api/knowledge/query",
            json={"query": "test query", "effort": "low"},
        )
        assert kq_resp.status == 200
        kq_data = await kq_resp.json()
        assert "result" in kq_data

        # 4. Conversations listing and switching
        convs_resp = await client.get("/api/conversations")
        assert convs_resp.status == 200
        convs_data = await convs_resp.json()
        assert "conversations" in convs_data

        curr_id = sess_data["conversation_id"]
        load_resp = await client.post(f"/api/conversations/{curr_id}/load")
        assert load_resp.status == 200
        load_data = await load_resp.json()
        assert load_data["status"] == "ok"
        assert load_data["conversation_id"] == curr_id

        # 5. Stop knowledge workers
        stop_resp = await client.post("/api/knowledge/stop")
        assert stop_resp.status == 200
        stop_data = await stop_resp.json()
        assert stop_data["status"] == "ok"

        # 6. Undo session turn
        undo_resp = await client.post("/api/session/undo", json={"message_id": 0})
        assert undo_resp.status == 200
        undo_data = await undo_resp.json()
        assert undo_data["status"] == "undone"
        assert undo_data["prompt"] == "Hello agent"

        # 7. Session reset
        reset_resp = await client.post("/api/session/reset")
        assert reset_resp.status == 200
        reset_data = await reset_resp.json()
        assert reset_data["status"] == "ok"



@pytest.mark.asyncio
async def test_websocket_event_streaming(test_harness: GeneralAgentHarness):
    """Test WebSocket connection greeting, prompt streaming, steer, and interrupt."""
    app = create_app(harness=test_harness)
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect("/ws/events")

        # 1. Initial connection greeting
        greeting = await ws.receive_json()
        assert greeting["type"] == "connection_established"
        assert greeting["project_id"] == test_harness.project_manager.project_id
        assert greeting["reasoning_effort"] == "medium"
        assert greeting["has_active_knowledge_workers"] is False

        # 2. Send prompt and receive streamed events
        await ws.send_json({"type": "prompt", "content": "Write hello world"})

        received_events = []
        for _ in range(20):
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                received_events.append(msg)
                if msg.get("type") == "turn_completed":
                    break
            except asyncio.TimeoutError:
                break

        assert any(e.get("type") == "token_chunk" for e in received_events)
        assert any(e.get("type") == "turn_completed" for e in received_events)

        # 3. Test continue mode over websocket
        await ws.send_json({"type": "continue", "content": "Keep working"})
        continue_events = []
        for _ in range(20):
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
                continue_events.append(msg)
                if msg.get("type") == "turn_completed":
                    break
            except asyncio.TimeoutError:
                break
        assert any(e.get("type") == "turn_completed" for e in continue_events)

        # 4. Test steer over websocket
        await ws.send_json({"type": "steer", "guidance": "Focus on python 3.12 syntax"})
        steer_ack = await ws.receive_json()
        assert steer_ack["type"] == "steer_result"

        # 5. Test sidecar query
        await ws.send_json({"type": "sidecar", "query": "What is the active branch?"})
        sidecar_resp = await ws.receive_json()
        assert sidecar_resp["type"] == "sidecar_response"

        # 6. Test interrupt
        await ws.send_json({"type": "interrupt", "reason": "user_stop"})
        int_resp = await ws.receive_json()
        assert int_resp["type"] == "interrupt_result"

        # 7. Test set_reasoning_effort over websocket
        await ws.send_json({"type": "set_reasoning_effort", "effort": "high"})
        effort_resp = await ws.receive_json()
        assert effort_resp["type"] == "reasoning_effort_updated"
        assert effort_resp["reasoning_effort"] == "high"
        assert test_harness.reasoning_effort == "high"

        # 8. Test stop_knowledge over websocket
        await ws.send_json({"type": "stop_knowledge"})
        stop_ack = await ws.receive_json()
        assert stop_ack["type"] == "stop_knowledge_result"
        worker_status = await ws.receive_json()
        assert worker_status["type"] == "knowledge_worker_status"
        assert worker_status["has_active_knowledge_workers"] is False

        # 9. Test undo over websocket
        await ws.send_json({"type": "undo", "message_id": 0})
        undo_resp = await ws.receive_json()
        assert undo_resp["type"] == "undo_result"
        assert undo_resp["status"] == "undone"

        await ws.close()


@pytest.mark.asyncio
async def test_web_static_spa_serving(test_harness: GeneralAgentHarness):
    """Test serving the built React SPA assets."""
    dist_dir = Path(__file__).parent.parent / "frontend" / "web" / "dist"
    app = create_app(harness=test_harness, static_dir=dist_dir)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/")
        assert resp.status == 200
        html = await resp.text()
        assert '<div id="root"></div>' in html
        assert "LibHippo Agent Console" in html
