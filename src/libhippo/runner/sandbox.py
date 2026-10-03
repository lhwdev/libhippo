"""Sandbox runner enforcing workspace containment and Bubblewrap container isolation."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libhippo.runner.project import ProjectManager, ProjectSecurityError


@dataclass
class BackgroundTaskInfo:
    """Tracks state and logs for a detached command process."""

    task_id: str
    command: str
    cwd: Path
    pid: int
    log_path: Path
    start_time: float
    process: asyncio.subprocess.Process
    is_daemon: bool = False
    exit_code: int | None = None
    output_buffer: str = ""


class SandboxRunner(ABC):
    """Abstract sandbox runner enforcing workspace boundaries."""

    @abstractmethod
    def validate_path(self, path: Path) -> Path:
        """Validate path resides inside workspace or scratch directory."""
        ...

    @abstractmethod
    async def run_command(
        self,
        command_line: str,
        cwd: Path | None = None,
        wait_ms: int = 2000,
        bypass_sandbox: bool = False,
        is_daemon: bool = False,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a shell command under sandbox security policies."""
        ...

    @abstractmethod
    async def manage_task(
        self,
        action: str,
        task_id: str,
        input: str | None = None,
    ) -> dict[str, Any]:
        """Inspect, await, terminate, or send input to background tasks."""
        ...


class BubblewrapSandboxRunner(SandboxRunner):
    """Linux Bubblewrap (bwrap) rootless sandbox runner with dynamic permission mounting."""

    def __init__(
        self,
        workspace_root: Path,
        project_manager: ProjectManager | None = None,
        scratch_dir: Path | None = None,
        tasks_dir: Path | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.project_manager = project_manager or ProjectManager(workspace_root=self.workspace_root)
        self.scratch_dir = (scratch_dir or (self.workspace_root / ".scratch")).resolve()
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir = (tasks_dir or (self.workspace_root / "tasks")).resolve()
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.bwrap_bin = shutil.which("bwrap")
        self.tasks: dict[str, BackgroundTaskInfo] = {}

    def validate_path(self, path: Path) -> Path:
        """Enforce path boundary containment inside workspace_root or scratch_dir."""
        resolved = path.resolve()
        in_ws = resolved == self.workspace_root or self.workspace_root in resolved.parents
        in_scratch = resolved == self.scratch_dir or self.scratch_dir in resolved.parents
        if not (in_ws or in_scratch):
            raise ProjectSecurityError(
                f"Path '{resolved}' escapes workspace boundary '{self.workspace_root}'"
            )
        return resolved

    _bwrap_functional: bool | None = None

    @classmethod
    def is_bwrap_supported(cls) -> bool:
        """Check if bwrap is available and functional in the host environment."""
        if cls._bwrap_functional is not None:
            return cls._bwrap_functional
        bwrap = shutil.which("bwrap")
        if not bwrap:
            cls._bwrap_functional = False
            return False
        import subprocess
        try:
            res = subprocess.run([bwrap, "--ro-bind", "/", "/", "true"], capture_output=True, timeout=1)
            cls._bwrap_functional = (res.returncode == 0)
        except Exception:
            cls._bwrap_functional = False
        return cls._bwrap_functional

    def build_bwrap_args(self, cwd: Path, bypass_sandbox: bool = False) -> list[str]:
        """Synthesize bwrap mounting arguments dynamically from permissions."""
        if bypass_sandbox or not self.is_bwrap_supported():
            return []

        args = [
            self.bwrap_bin,
            "--ro-bind", "/", "/",
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
            "--unshare-pid",
            "--unshare-ipc",
        ]

        # Allowed read-write mounts
        args.extend(["--bind", str(self.workspace_root), str(self.workspace_root)])
        if self.scratch_dir.exists():
            args.extend(["--bind", str(self.scratch_dir), str(self.scratch_dir)])

        # Network namespace isolation
        if not self.project_manager.is_network_allowed():
            args.append("--unshare-net")

        args.extend(["--chdir", str(cwd)])
        return args

    async def run_command(
        self,
        command_line: str,
        cwd: Path | None = None,
        wait_ms: int = 2000,
        bypass_sandbox: bool = False,
        is_daemon: bool = False,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a shell command with security boundary enforcement and background detachment."""
        tid = task_id or f"task-{uuid.uuid4().hex[:8]}"
        effective_cwd = self.validate_path(cwd or self.workspace_root)

        # Policy check
        cmd_decision = self.project_manager.check_command(command_line)
        if cmd_decision == "deny":
            raise ProjectSecurityError(f"Command '{command_line}' denied by project security policy.")

        log_path = self.tasks_dir / f"{tid}.log"
        log_file = open(log_path, "w", encoding="utf-8")

        bwrap_args = self.build_bwrap_args(effective_cwd, bypass_sandbox=bypass_sandbox)
        if bwrap_args:
            exec_args = bwrap_args + ["bash", "-c", command_line]
        else:
            exec_args = ["bash", "-c", command_line]

        process = await asyncio.create_subprocess_exec(
            *exec_args,
            cwd=str(effective_cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            preexec_fn=os.setsid,
        )

        task_info = BackgroundTaskInfo(
            task_id=tid,
            command=command_line,
            cwd=effective_cwd,
            pid=process.pid,
            log_path=log_path,
            start_time=time.time(),
            process=process,
            is_daemon=is_daemon,
        )
        self.tasks[tid] = task_info

        # Monitor output stream asynchronously
        async def stream_output() -> None:
            try:
                assert process.stdout is not None
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace")
                    task_info.output_buffer += text
                    log_file.write(text)
                    log_file.flush()
                task_info.exit_code = await process.wait()
            finally:
                log_file.close()

        stream_task = asyncio.create_task(stream_output())

        # Wait for wait_ms ceiling
        wait_seconds = max(0.05, wait_ms / 1000.0)
        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=wait_seconds)
            await stream_task
            return {
                "status": "completed",
                "task_id": tid,
                "exit_code": process.returncode or 0,
                "output": task_info.output_buffer,
                "log_path": str(log_path),
            }
        except asyncio.TimeoutError:
            # Detach to background
            return {
                "status": "running",
                "task_id": tid,
                "pid": process.pid,
                "output_so_far": task_info.output_buffer,
                "log_path": str(log_path),
            }

    async def manage_task(
        self,
        action: str,
        task_id: str,
        input: str | None = None,
    ) -> dict[str, Any]:
        """Manage active or completed background tasks."""
        if task_id not in self.tasks:
            return {"status": "error", "error": f"Task '{task_id}' not found."}

        task = self.tasks[task_id]
        if action == "status":
            is_alive = task.process.returncode is None
            return {
                "task_id": task_id,
                "status": "running" if is_alive else "completed",
                "exit_code": task.process.returncode,
                "output_length": len(task.output_buffer),
                "log_path": str(task.log_path),
            }
        elif action == "wait":
            await task.process.wait()
            return {
                "task_id": task_id,
                "status": "completed",
                "exit_code": task.process.returncode,
                "output": task.output_buffer,
                "log_path": str(task.log_path),
            }
        elif action == "kill":
            if task.process.returncode is None:
                try:
                    os.killpg(os.getpgid(task.process.pid), signal.SIGINT)
                    await asyncio.sleep(0.5)
                    if task.process.returncode is None:
                        os.killpg(os.getpgid(task.process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            return {"task_id": task_id, "status": "killed"}
        elif action == "send_input":
            if task.process.stdin and task.process.returncode is None:
                if input:
                    task.process.stdin.write(input.encode("utf-8"))
                    await task.process.stdin.drain()
                return {"task_id": task_id, "status": "input_sent"}
            return {"task_id": task_id, "status": "error", "error": "Cannot send input: process closed or no stdin"}

        return {"status": "error", "error": f"Unknown action '{action}'"}
