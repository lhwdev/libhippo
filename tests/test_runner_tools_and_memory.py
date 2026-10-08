"""Tests for ContextMemory, WorkloadGovernor, and CodingToolSuite (modular tools)."""

import asyncio
from pathlib import Path
import pytest

from libhippo.runner.config import HarnessConfig
from libhippo.runner.governor import CircuitBreakerTrippedError, MaxTurnsExceededError, WorkloadGovernor
from libhippo.runner.memory import ContextMemory
from libhippo.runner.project import ProjectManager
from libhippo.runner.sandbox import BubblewrapSandboxRunner
from libhippo.runner.tools import CodingToolSuite
from libhippo.storage.store import KnowledgeStore
from libhippo.tools.registry import ToolRegistry


def test_context_memory_zone3_compaction():
    """Verify Zone 1 bitwise static prefix, Zone 2 metadata, and Zone 3 eviction target."""
    mem = ContextMemory(model_name="gpt-4o")
    mem.set_zone1_prefix(
        system_persona="You are a coding agent.",
        repo_profile="A Python repository.",
        tool_definitions=[{"name": "read_file", "description": "read files"}],
    )
    assert len(mem.zone1_prefix) == 1
    z1_tokens = mem.zone1_prefix[0].raw_token_count

    # User turn with temporal metadata
    u_msg = mem.append_user_turn("Run tests", timestamp="2026-10-03T18:00:00Z", branch="feature")
    assert '<USER_PROMPT timestamp="2026-10-03T18:00:00Z"' in u_msg.content

    # Bulky tool outputs
    large_log = "error: line\n" * 200
    t_msg1 = mem.append_tool_output("run_command", large_log, is_evictable=True, file_path_reference="build.log")
    t_msg2 = mem.append_tool_output("read_file", large_log, is_evictable=True, file_path_reference="src/app.py")

    total_before = mem.get_total_tokens()
    assert total_before > 200

    # Compact down to target tokens
    evicted = mem.compact_zone3(target_tokens=z1_tokens + 50)
    assert evicted > 0
    assert mem.get_total_tokens() < total_before
    # First tool output should be compacted to reference
    assert "[Referenced: build.log]" in t_msg1.content
    assert t_msg1.zone == "zone3_compacted"


def test_workload_governor_limits():
    """Verify governor turn limits, soft watermark warning, and circuit breaker."""
    cfg = HarnessConfig(max_turns=3, soft_token_watermark=100, hard_token_limit=300)
    mem = ContextMemory()
    gov = WorkloadGovernor(config=cfg, memory=mem)

    # Turns
    gov.start_turn()
    gov.start_turn()
    gov.start_turn()
    with pytest.raises(MaxTurnsExceededError):
        gov.start_turn()

    # Circuit breaker
    gov.record_tool_result("read_file", False, "File not found")
    gov.record_tool_result("read_file", False, "File not found")
    with pytest.raises(CircuitBreakerTrippedError):
        gov.record_tool_result("read_file", False, "File not found")


@pytest.mark.asyncio
async def test_coding_tool_suite_filesystem(tmp_path: Path):
    """Verify read_file, overwrite_file, write_file (replace/target), delete_file."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    cfg = HarnessConfig(workspace_root=ws, user_config_dir=tmp_path / "cfg")
    pm = ProjectManager(workspace_root=ws, config=cfg)
    sandbox = BubblewrapSandboxRunner(workspace_root=ws, project_manager=pm, tasks_dir=ws / "tasks")
    suite = CodingToolSuite(workspace_root=ws, sandbox=sandbox, project_manager=pm)

    # 1. overwrite_file
    res = await suite.overwrite_file("test.py", "line 1\nline 2\nline 3\n")
    assert "Successfully wrote" in res
    assert (ws / "test.py").is_file()

    # 2. read_file
    read_out = await suite.read_file("test.py", start_line=1, end_line=2)
    assert "1: line 1" in read_out
    assert "2: line 2" in read_out

    # 3. write_file line replacement
    await suite.write_file("test.py", "new line 2", start_line=2, end_line=2)
    view2 = await suite.read_file("test.py")
    assert "new line 2" in view2

    # 5. write_file target search-and-replace
    await suite.write_file("test.py", "replaced line 1", target="line 1")
    view3 = await suite.read_file("test.py")
    assert "replaced line 1" in view3

    # 6. delete_file
    await suite.delete_file("test.py")
    assert not (ws / "test.py").exists()


@pytest.mark.asyncio
async def test_pure_python_search_file_with_gitignore(tmp_path: Path):
    """Verify pure Python search_file respecting .gitignore, hidden files, and glob filters."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    src_dir = ws / "src"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("def hello():\n    print('Hello World')\n", encoding="utf-8")
    (src_dir / "util.ts").write_text("export function helper() { return 'Hello TS'; }\n", encoding="utf-8")

    ignored_dir = ws / "node_modules"
    ignored_dir.mkdir()
    (ignored_dir / "index.js").write_text("// Hello node_modules\n", encoding="utf-8")

    (ws / ".gitignore").write_text("node_modules/\n*.secret\n", encoding="utf-8")
    (ws / "data.secret").write_text("Hello Secret Token\n", encoding="utf-8")

    cfg = HarnessConfig(workspace_root=ws, user_config_dir=tmp_path / "cfg")
    pm = ProjectManager(workspace_root=ws, config=cfg)
    sandbox = BubblewrapSandboxRunner(workspace_root=ws, project_manager=pm, tasks_dir=ws / "tasks")
    suite = CodingToolSuite(workspace_root=ws, sandbox=sandbox, project_manager=pm)

    # Search pattern 'Hello' - should find main.py and util.ts, but NOT node_modules or data.secret
    res = await suite.search_file("Hello")
    assert "src/main.py:2:    print('Hello World')" in res
    assert "src/util.ts:1:export function helper() { return 'Hello TS'; }" in res
    assert "node_modules" not in res
    assert "data.secret" not in res

    # Glob filter
    res_py = await suite.search_file("Hello", glob="*.py")
    assert "src/main.py" in res_py
    assert "src/util.ts" not in res_py

    # no_ignore=True should find ignored files
    res_all = await suite.search_file("Hello", no_ignore=True)
    assert "node_modules/index.js" in res_all

    # Explicit search inside ignored dir should work!
    res_in_ignored = await suite.search_file("Hello", path="node_modules")
    assert "node_modules/index.js:1:// Hello node_modules" in res_in_ignored

    # list_dir root label is ./ and ignored dirs show (ignored)
    ld_root = await suite.list_dir()
    assert ld_root.startswith("./\n")
    assert "node_modules/ (ignored)" in ld_root
    assert "node_modules/index.js" not in ld_root

    # list_dir targeting ignored dir lists its contents
    ld_ignored = await suite.list_dir(path="node_modules")
    assert "node_modules/" in ld_ignored
    assert "index.js" in ld_ignored


@pytest.mark.asyncio
async def test_modify_knowledge_tool(tmp_path: Path):
    """Verify modify_knowledge is excluded from harness tools and exclusive to VerifierAgent."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    k_dir = tmp_path / "knowledge"

    async with KnowledgeStore(root_dir=k_dir) as store:
        cfg = HarnessConfig(workspace_root=ws, user_config_dir=tmp_path / "cfg")
        pm = ProjectManager(workspace_root=ws, config=cfg)
        sandbox = BubblewrapSandboxRunner(workspace_root=ws, project_manager=pm, tasks_dir=ws / "tasks")
        suite = CodingToolSuite(workspace_root=ws, sandbox=sandbox, project_manager=pm, store=store)

        # Harness tool suite must NOT expose modify_knowledge to TaskRunner / Coding agents
        assert "modify_knowledge" not in suite.tools

        # VerifierAgent remains equipped with modify_knowledge tool via ToolRegistry
        reg = ToolRegistry(store=store)
        verifier_modify_tool = reg.get_modify_knowledge_tool()
        create_res = await verifier_modify_tool(
            action="create",
            path="common/test_rule.md",
            content="# Test Rule\nAlways write unit tests.\n",
            metadata={"title": "Testing Standard", "confidence": 0.95},
        )
        assert create_res["status"] == "success"

        # Verify node readable through store
        node = await store.get_node("common/test_rule.md")
        assert node is not None
        assert "Always write unit tests" in node.body


def test_context_memory_zone2_summarization_compaction():
    """Verify Zone 2 turn summarization when is_evictable=False messages exceed budget."""
    mem = ContextMemory(model_name="gpt-4o")
    mem.set_zone1_prefix(system_persona="You are a coding assistant.")

    # Add multiple non-evictable turns
    u1 = mem.append_user_turn("First request: setup backend database schema.")
    a1 = mem.append_assistant_turn("Set up SQLite schema with users and articles tables.")
    u2 = mem.append_user_turn("Second request: implement authentication endpoints.")
    a2 = mem.append_assistant_turn("Implemented JWT auth endpoints in auth.py.")
    u3 = mem.append_user_turn("Third request: add unit tests for auth.")
    a3 = mem.append_assistant_turn("Added test_auth.py with 5 passing tests.")
    u4 = mem.append_user_turn("Fourth request: fix edge cases in refresh token.")
    a4 = mem.append_assistant_turn("Fixed refresh token edge cases.")

    total_tokens_before = mem.get_total_tokens()
    evicted = mem.compact_memory(target_tokens=50)
    assert evicted > 0
    assert mem.get_total_tokens() < total_tokens_before

    # Verify head is preserved
    assert mem.zone2_history[0] is u1
    # Verify intermediate summary is inserted
    summary_msg = mem.zone2_history[1]
    assert summary_msg.metadata.get("is_summary") is True
    assert "<CONVERSATION_SUMMARY" in summary_msg.content
    assert "</CONVERSATION_SUMMARY>" in summary_msg.content
    # Verify tail turns are preserved
    assert mem.zone2_history[-1] is a4

