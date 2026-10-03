"""Tests for ProjectManager and BubblewrapSandboxRunner."""

import asyncio
from pathlib import Path
import pytest

from libhippo.runner.config import HarnessConfig
from libhippo.runner.project import ProjectManager, ProjectSecurityError
from libhippo.runner.sandbox import BubblewrapSandboxRunner


def test_project_manager_policy_checks(tmp_path: Path):
    """Verify ProjectManager allow/deny/ask checks."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    user_dir = tmp_path / "user_config"
    cfg = HarnessConfig(workspace_root=ws, user_config_dir=user_dir)
    pm = ProjectManager(workspace_root=ws, config=cfg)

    # Check defaults
    assert pm.check_read(ws / "src" / "main.py") == "allow"
    assert pm.check_read(ws / ".env") == "deny"
    assert pm.check_read(ws / "secrets" / "key.pem") == "deny"

    assert pm.check_write(ws / "src" / "app.py") == "allow"
    assert pm.check_write(ws / ".git" / "config") == "deny"

    assert pm.check_command("git status") == "allow"
    assert pm.check_command("rm -rf /") == "deny"


@pytest.mark.asyncio
async def test_sandbox_path_validation(tmp_path: Path):
    """Verify SandboxRunner restricts paths to workspace root."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    cfg = HarnessConfig(workspace_root=ws, user_config_dir=tmp_path / "cfg")
    pm = ProjectManager(workspace_root=ws, config=cfg)
    runner = BubblewrapSandboxRunner(workspace_root=ws, project_manager=pm)

    # In workspace
    valid = runner.validate_path(ws / "foo.txt")
    assert valid == (ws / "foo.txt").resolve()

    # Out of workspace
    with pytest.raises(ProjectSecurityError):
        runner.validate_path(tmp_path / "other" / "file.txt")


@pytest.mark.asyncio
async def test_sandbox_run_command_fast(tmp_path: Path):
    """Verify synchronous fast command execution."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    cfg = HarnessConfig(workspace_root=ws, user_config_dir=tmp_path / "cfg")
    pm = ProjectManager(workspace_root=ws, config=cfg)
    runner = BubblewrapSandboxRunner(workspace_root=ws, project_manager=pm, tasks_dir=ws / "tasks")

    res = await runner.run_command("echo 'hello libhippo'", wait_ms=2000)
    assert res["status"] == "completed"
    assert "hello libhippo" in res["output"]
    assert res["exit_code"] == 0


@pytest.mark.asyncio
async def test_sandbox_background_detach_and_manage(tmp_path: Path):
    """Verify background detachment and manage_task inspection."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    cfg = HarnessConfig(workspace_root=ws, user_config_dir=tmp_path / "cfg")
    pm = ProjectManager(workspace_root=ws, config=cfg)
    runner = BubblewrapSandboxRunner(workspace_root=ws, project_manager=pm, tasks_dir=ws / "tasks")

    # Command sleeps for 1 second, wait_ms is 50ms -> should detach
    res = await runner.run_command("sleep 0.5 && echo 'done sleeping'", wait_ms=50)
    assert res["status"] == "running"
    tid = res["task_id"]

    # Wait for completion
    wait_res = await runner.manage_task(action="wait", task_id=tid)
    assert wait_res["status"] == "completed"
    assert wait_res["exit_code"] == 0
    assert "done sleeping" in wait_res["output"]
