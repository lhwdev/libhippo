"""Persistent multi-session conversation tracking grouped under projects."""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from libhippo.runner.config import HarnessConfig
from libhippo.runner.types import ContextMessage
from libhippo.config.paths import get_user_config_dir


class ConversationSession:
    """Manages persistent conversation state, subagents, artifacts, and task logs."""

    def __init__(
        self,
        project_id: str,
        conversation_id: str,
        storage_dir: Path | None = None,
        config: HarnessConfig | None = None,
        name: str | None = None,
    ) -> None:
        self.project_id = project_id
        self.conversation_id = conversation_id
        if storage_dir:
            self.storage_dir = storage_dir.resolve()
        elif config:
            self.storage_dir = (config.get_conversations_dir() / conversation_id).resolve()
        else:
            self.storage_dir = (get_user_config_dir() / "projects" / project_id / "conversations" / conversation_id).resolve()

        self.artifacts_dir = self.storage_dir / "artifacts"
        self.tasks_dir = self.storage_dir / "tasks"
        self.subagents_dir = self.storage_dir / "subagents"
        self.transcript_file = self.storage_dir / "transcript.jsonl"
        self.metadata_file = self.storage_dir / "metadata.json"
        self._ensure_storage_dir()
        if not self.metadata_file.exists():
            initial_name = name or self._generate_default_name()
            self.save_metadata(self._create_default_metadata(name=initial_name))
        elif name:
            self.set_name(name)

    @property
    def session_dir(self) -> Path:
        """Alias to storage_dir for compatibility."""
        return self.storage_dir

    def _ensure_storage_dir(self) -> None:
        """Create storage root directory safely."""
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _ensure_dirs(self) -> None:
        """Backwards compatibility alias for _ensure_storage_dir."""
        self._ensure_storage_dir()

    def _current_timestamp(self) -> str:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _generate_default_name(self) -> str:
        return f"Conversation {self.conversation_id[-6:]}"

    def _create_default_metadata(self, name: str | None = None) -> dict[str, Any]:
        ts = self._current_timestamp()
        return {
            "id": self.conversation_id,
            "name": name or self._generate_default_name(),
            "project_id": self.project_id,
            "created_at": ts,
            "updated_at": ts,
            "message_count": 0,
        }

    def get_metadata(self) -> dict[str, Any]:
        """Read metadata.json or return default metadata."""
        if self.metadata_file.is_file():
            try:
                return json.loads(self.metadata_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return self._create_default_metadata()

    def save_metadata(self, meta: dict[str, Any]) -> None:
        """Write metadata to metadata.json."""
        self._ensure_storage_dir()
        try:
            self.metadata_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except OSError:
            pass

    def get_name(self) -> str:
        """Get conversation human-readable name."""
        meta = self.get_metadata()
        return meta.get("name") or self.get_title()

    def set_name(self, name: str) -> None:
        """Explicitly set conversation name."""
        meta = self.get_metadata()
        meta["name"] = name
        meta["updated_at"] = self._current_timestamp()
        self.save_metadata(meta)

    def _extract_name_from_user_prompt(self, prompt: str) -> str:
        """Extract a clean, concise title from user prompt."""
        match = re.search(r"<USER_PROMPT[^>]*>(?:(.*?)</USER_PROMPT>|(.*))", prompt, flags=re.DOTALL | re.IGNORECASE)
        if match:
            content = (match.group(1) if match.group(1) is not None else match.group(2)).strip()
        else:
            content = prompt
            if "<session_context>" in content and "</session_context>" in content:
                content = content.split("</session_context>")[-1].strip()
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if not lines:
            return self._generate_default_name()
        first_line = lines[0].lstrip("#-* >").strip()
        if len(first_line) > 50:
            truncated = first_line[:50]
            last_space = truncated.rfind(" ")
            if last_space > 20:
                truncated = truncated[:last_space]
            return truncated + "..."
        return first_line or self._generate_default_name()

    async def append_message(self, message: ContextMessage) -> None:
        """Append message record atomically to transcript.jsonl and maintain metadata."""
        self._ensure_storage_dir()
        line = json.dumps(asdict(message)) + "\n"
        try:
            with open(self.transcript_file, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError:
            pass

        meta = self.get_metadata()
        meta["updated_at"] = self._current_timestamp()
        meta["message_count"] = meta.get("message_count", 0) + 1

        if message.role == "user":
            curr_name = meta.get("name", "")
            if not curr_name or curr_name.startswith("Conversation ") or curr_name.startswith("New Conversation") or curr_name == self.conversation_id:
                meta["name"] = self._extract_name_from_user_prompt(message.content)

        self.save_metadata(meta)

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

    def truncate_before(self, message_index: int) -> list[ContextMessage]:
        """Truncate transcript.jsonl and history before message_index (retaining [:message_index])."""
        messages = self.get_messages()
        kept = messages[:message_index] if 0 <= message_index < len(messages) else messages
        self._ensure_storage_dir()
        try:
            with open(self.transcript_file, "w", encoding="utf-8") as f:
                for m in kept:
                    f.write(json.dumps(asdict(m)) + "\n")
        except OSError:
            pass

        meta = self.get_metadata()
        meta["updated_at"] = self._current_timestamp()
        meta["message_count"] = len(kept)
        self.save_metadata(meta)
        return kept

    async def record_artifact(
        self,
        name: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Persist structured report or diff artifact with metadata (creates artifacts/ on demand)."""
        self._ensure_storage_dir()
        try:
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

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
        """Update background task record (creates tasks/ on demand)."""
        self._ensure_storage_dir()
        try:
            self.tasks_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        task_file = self.tasks_dir / f"{task_id}.json"
        try:
            task_file.write_text(json.dumps(info, indent=2), encoding="utf-8")
        except OSError:
            pass

    async def record_subagent(self, subagent_id: str, info: dict[str, Any]) -> None:
        """Update subagent record (creates subagents/ on demand)."""
        self._ensure_storage_dir()
        try:
            self.subagents_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

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

    def get_title(self) -> str:
        """Derive short conversation title from metadata or first user message."""
        meta = self.get_metadata()
        if meta.get("name"):
            return meta["name"]
        for msg in self.get_messages():
            if msg.role == "user":
                return self._extract_name_from_user_prompt(msg.content)
        return self._generate_default_name()

    def get_updated_at(self) -> str:
        """Get last modified timestamp string."""
        meta = self.get_metadata()
        if meta.get("updated_at"):
            return meta["updated_at"]
        if self.transcript_file.exists():
            mtime = self.transcript_file.stat().st_mtime
            return datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        return ""


def context_messages_to_chat_messages(messages: list[ContextMessage]) -> list[dict[str, Any]]:
    """Convert ContextMessage stream into UI-friendly ChatMessage list."""
    chat_msgs: list[dict[str, Any]] = []

    for i, m in enumerate(messages):
        if m.zone == "zone1_prefix":
            continue

        role = m.role
        ts = m.metadata.get("timestamp", "")
        if not ts and role == "user":
            ts_match = re.search(r'<USER_PROMPT[^>]*timestamp="([^"]+)"', m.content, flags=re.IGNORECASE)
            if ts_match:
                ts = ts_match.group(1)

        if isinstance(ts, str) and "T" in ts:
            try:
                time_part = ts.split("T")[1].split(".")[0]
                if "+" in time_part:
                    time_part = time_part.split("+")[0]
                ts = time_part
            except Exception:
                pass

        if role == "user":
            content = m.content
            match = re.search(r"<USER_PROMPT[^>]*>(?:(.*?)</USER_PROMPT>|(.*))", content, flags=re.DOTALL | re.IGNORECASE)
            if match:
                content = (match.group(1) if match.group(1) is not None else match.group(2)).strip()
            else:
                if "<session_context>" in content and "</session_context>" in content:
                    content = content.split("</session_context>")[-1].lstrip("\n")
            chat_msgs.append({
                "id": f"user-{i}",
                "role": "user",
                "content": content,
                "timestamp": ts,
                "message_index": i,
                "raw_prompt": content,
            })
        elif role == "assistant":
            tool_calls = m.metadata.get("tool_calls")
            if tool_calls:
                parsed_tools = []
                for tc in tool_calls:
                    raw_args = tc.get("arguments", "{}")
                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                        except Exception:
                            args = {}
                    else:
                        args = raw_args
                    parsed_tools.append({
                        "id": tc.get("id", f"call_{i}"),
                        "name": tc.get("name", "tool"),
                        "arguments": args,
                        "result": None,
                        "error": None,
                    })
                chat_msgs.append({
                    "id": f"asst-{i}",
                    "role": "assistant",
                    "content": "",
                    "toolCalls": parsed_tools,
                    "timestamp": ts,
                })
            else:
                if chat_msgs and chat_msgs[-1]["role"] == "assistant" and not chat_msgs[-1]["content"] and chat_msgs[-1].get("toolCalls"):
                    chat_msgs[-1]["content"] = m.content
                else:
                    chat_msgs.append({
                        "id": f"asst-{i}",
                        "role": "assistant",
                        "content": m.content,
                        "timestamp": ts,
                    })
        elif role == "tool":
            call_id = m.tool_call_id
            matched = False
            for prev in reversed(chat_msgs):
                if prev["role"] == "assistant" and prev.get("toolCalls"):
                    for tc in prev["toolCalls"]:
                        if tc["id"] == call_id:
                            tc["result"] = m.content
                            if m.metadata.get("is_error"):
                                tc["error"] = m.content
                            matched = True
                            break
                if matched:
                    break
        elif role in ("system", "interrupt"):
            chat_msgs.append({
                "id": f"{role}-{i}",
                "role": role,
                "content": m.content,
                "timestamp": ts,
            })

    return chat_msgs

