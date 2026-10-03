"""Persistent multi-session conversation tracking grouped under projects."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from libhippo.runner.config import HarnessConfig
from libhippo.runner.types import ContextMessage


class ConversationSession:
    """Manages persistent conversation state, subagents, artifacts, and task logs."""

    def __init__(
        self,
        project_id: str,
        conversation_id: str,
        storage_dir: Path | None = None,
        config: HarnessConfig | None = None,
    ) -> None:
        self.project_id = project_id
        self.conversation_id = conversation_id
        if storage_dir:
            self.storage_dir = storage_dir.resolve()
        elif config:
            self.storage_dir = (config.get_conversations_dir() / conversation_id).resolve()
        else:
            self.storage_dir = (Path.home() / ".config" / "libhippo" / "projects" / project_id / "conversations" / conversation_id).resolve()

        self.artifacts_dir = self.storage_dir / "artifacts"
        self.tasks_dir = self.storage_dir / "tasks"
        self.subagents_dir = self.storage_dir / "subagents"
        self.transcript_file = self.storage_dir / "transcript.jsonl"
        self._ensure_dirs()

    @property
    def session_dir(self) -> Path:
        """Alias to storage_dir for compatibility."""
        return self.storage_dir

    def _ensure_dirs(self) -> None:
        """Create directory hierarchy safely."""
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
            self.tasks_dir.mkdir(parents=True, exist_ok=True)
            self.subagents_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    async def append_message(self, message: ContextMessage) -> None:
        """Append message record atomically to transcript.jsonl."""
        self._ensure_dirs()
        line = json.dumps(asdict(message)) + "\n"
        try:
            with open(self.transcript_file, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

    def get_messages(self) -> list[ContextMessage]:
        """Read full transcript back into ContextMessage list."""
        if not self.transcript_file.is_file():
            return []
        messages: list[ContextMessage] = []
        with open(self.transcript_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    messages.append(ContextMessage(**data))
                except Exception:
                    pass
        return messages

    async def record_artifact(
        self,
        name: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Persist structured report or diff artifact with metadata."""
        self._ensure_dirs()
        clean_name = name.replace("/", "_").replace("\\", "_")
        if not clean_name.endswith(".md"):
            clean_name = f"{clean_name}.md"

        artifact_path = self.artifacts_dir / clean_name
        meta_path = self.artifacts_dir / f"{clean_name}.meta.json"

        try:
            artifact_path.write_text(content, encoding="utf-8")
            meta = metadata or {}
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except OSError:
            pass

        return artifact_path

    def list_artifacts(self) -> list[dict[str, Any]]:
        """List all artifacts saved in this conversation."""
        if not self.artifacts_dir.is_dir():
            return []
        results: list[dict[str, Any]] = []
        for p in self.artifacts_dir.glob("*.md"):
            meta_file = p.with_suffix(".md.meta.json")
            meta = {}
            if meta_file.is_file():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except Exception:
                    pass
            results.append({
                "name": p.name,
                "path": str(p),
                "metadata": meta,
            })
        return results

    async def record_task(self, task_id: str, info: dict[str, Any]) -> None:
        """Update background task record."""
        self._ensure_dirs()
        task_file = self.tasks_dir / f"{task_id}.json"
        try:
            task_file.write_text(json.dumps(info, indent=2), encoding="utf-8")
        except OSError:
            pass

    async def record_subagent(self, subagent_id: str, info: dict[str, Any]) -> None:
        """Update subagent record."""
        self._ensure_dirs()
        sub_file = self.subagents_dir / f"{subagent_id}.json"
        try:
            sub_file.write_text(json.dumps(info, indent=2), encoding="utf-8")
        except OSError:
            pass

    def list_subagents(self) -> list[dict[str, Any]]:
        """List all tracked subagents."""
        if not self.subagents_dir.is_dir():
            return []
        subagents: list[dict[str, Any]] = []
        for p in self.subagents_dir.glob("*.json"):
            try:
                subagents.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass
        return subagents
