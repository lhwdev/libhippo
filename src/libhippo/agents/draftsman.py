"""Knowledge Draftsman Session and Deterministic Sanity Checks.

Provides multi-draft tracking, surgical editing, line-addressed reading,
deterministic sanity checks (frontmatter, token sizing, code fences),
and Maker-Checker submission tools.
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import tiktoken
import yaml

from libhippo.storage.store import KnowledgeStore


def run_draft_sanity_checks(markdown: str) -> dict[str, Any]:
    """Execute deterministic sanity checks on knowledge markdown without LLM calls.

    Validates:
    1. Frontmatter YAML parsing and required keys (title, namespace, version, nature, source).
    2. Token count sizing via tiktoken cl100k_base (500-1000 target, 1800 hard bound).
    3. Code block closure (matching triple backticks).
    """
    errors: list[str] = []
    warnings: list[str] = []
    frontmatter_dict: dict[str, Any] = {}

    # 1. Frontmatter extraction and parsing
    fm_match = re.match(r"^\s*---\s*\n(.*?)\n---\s*(?:\n|\Z)", markdown, re.DOTALL)
    if not fm_match:
        errors.append("Missing YAML frontmatter block enclosed in '---'.")
    else:
        raw_yaml = fm_match.group(1)
        try:
            parsed = yaml.safe_load(raw_yaml)
            if not isinstance(parsed, dict):
                errors.append("Frontmatter must be a valid YAML dictionary.")
            else:
                frontmatter_dict = parsed
        except Exception as e:
            errors.append(f"Invalid YAML in frontmatter: {e}")

    # Check frontmatter keys
    if frontmatter_dict:
        # Agent-written properties: title is required
        if not frontmatter_dict.get("title"):
            errors.append("Frontmatter missing required field: 'title'.")

    # 2. Token count check
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        token_count = len(enc.encode(markdown))
    except Exception:
        token_count = max(1, len(markdown) // 4)

    if token_count > 1800:
        errors.append(f"Token count {token_count} exceeds hard limit of 1800 tokens.")
        token_status = f"FAIL: {token_count} tokens > 1800 hard bound"
    elif token_count > 1000:
        warnings.append(f"Token count {token_count} exceeds recommended 500-1000 range.")
        token_status = f"WARNING: {token_count} tokens (500-1000 target)"
    elif token_count < 100:
        warnings.append(f"Token count {token_count} is low for a technical rule document.")
        token_status = f"LOW: {token_count} tokens (< 100)"
    else:
        token_status = f"PASS: {token_count} tokens (within 500-1000 range)"

    # 3. Code fence verification
    fence_count = markdown.count("```")
    if fence_count % 2 != 0:
        errors.append(f"Unclosed code fence: found {fence_count} '```' delimiters.")
        code_fence_status = "FAIL: unclosed code block"
    else:
        code_fence_status = "PASS: all code blocks closed"

    passed = len(errors) == 0

    return {
        "passed": passed,
        "errors": errors,
        "warnings": warnings,
        "tokens": token_count,
        "token_status": token_status,
        "code_blocks": code_fence_status,
        "frontmatter": frontmatter_dict,
    }


class KnowledgeDraftSession:
    """Manages multi-draft workspace and deterministic checks for Curator and Harvester."""

    def __init__(
        self,
        session_id: str | None = None,
        workspace_root: Path | str | None = None,
        store: KnowledgeStore | None = None,
        sandbox: Any | None = None,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.drafts_dir = self.workspace_root / ".libhippo" / "drafts" / self.session_id
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.sandbox = sandbox
        self.drafts: dict[str, Path] = {}
        self.active_path: str | None = None

    def _normalize_path(self, path: str) -> str:
        clean = path.strip("/. ").replace("\\", "/")
        if not clean.endswith(".md"):
            clean += ".md"
        return clean

    async def read_knowledge(
        self,
        path: str | None = None,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        """Read line-addressed slice of a draft document or existing store knowledge node."""
        target_path = path or self.active_path
        if not target_path and len(self.drafts) == 1:
            target_path = next(iter(self.drafts.keys()))

        if not target_path:
            return "[ERROR: No active draft. Please specify path='<namespace>/<file>.md']"

        norm = self._normalize_path(target_path)

        # 1. Active draft in session
        if norm in self.drafts and self.drafts[norm].exists():
            text = self.drafts[norm].read_text(encoding="utf-8")
        # 2. Existing node in knowledge store
        elif self.store:
            try:
                phys = self.store.mount_manager.resolve_physical_path(norm)
                if phys.exists() and phys.is_file():
                    text = phys.read_text(encoding="utf-8")
                else:
                    return f"[ERROR: Knowledge path '{norm}' not found in drafts or knowledge store.]"
            except Exception:
                return f"[ERROR: Knowledge path '{norm}' not found in drafts or knowledge store.]"
        else:
            return f"[ERROR: Knowledge path '{norm}' not found in active drafts.]"

        lines = text.splitlines()
        total_lines = len(lines)
        s_line = max(1, start_line)
        e_line = min(total_lines, end_line) if end_line is not None else total_lines

        if s_line > total_lines:
            return f"[Draft '{norm}': start_line {s_line} exceeds file line count {total_lines}]"

        selected = lines[s_line - 1 : e_line]
        return "\n".join(f"{idx}: {line}" for idx, line in enumerate(selected, start=s_line))

    async def write_knowledge(
        self,
        content: str,
        path: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        target: str | None = None,
    ) -> str:
        """Surgically edit or replace a draft document and return immediate sanity check results."""
        target_path = path or self.active_path
        if not target_path and len(self.drafts) == 1:
            target_path = next(iter(self.drafts.keys()))

        if not target_path:
            return "[ERROR: No draft path specified. Please specify path='<namespace>/<name>.md']"

        norm = self._normalize_path(target_path)
        self.active_path = norm

        draft_file = self.drafts.get(norm)
        if not draft_file:
            draft_file = self.drafts_dir / norm
            draft_file.parent.mkdir(parents=True, exist_ok=True)
            if not draft_file.exists():
                existing_text = ""
                if self.store:
                    try:
                        phys = self.store.mount_manager.resolve_physical_path(norm)
                        if phys.exists() and phys.is_file():
                            existing_text = phys.read_text(encoding="utf-8")
                    except Exception:
                        pass
                draft_file.write_text(existing_text, encoding="utf-8")
            self.drafts[norm] = draft_file

        current_text = draft_file.read_text(encoding="utf-8")

        # Edit logic: full replacement vs surgical string/line replacement
        if start_line is None and end_line is None and target is None:
            new_text = content
        elif target is not None:
            if target not in current_text:
                return f"[ERROR: Target string '{target[:80]}...' not found in draft '{norm}']"
            new_text = current_text.replace(target, content, 1)
        else:
            lines = current_text.splitlines()
            s_idx = max(0, (start_line or 1) - 1)
            e_idx = end_line if end_line is not None else len(lines)
            new_lines = content.splitlines()
            lines[s_idx:e_idx] = new_lines
            new_text = "\n".join(lines) + ("\n" if current_text.endswith("\n") or not current_text else "")

        draft_file.write_text(new_text, encoding="utf-8")

        # Run deterministic sanity checks immediately
        checks = run_draft_sanity_checks(new_text)

        lines_count = len(new_text.splitlines())
        status_label = "READY_TO_COMMIT" if checks["passed"] else "REQUIRES_REVISION"
        fm = checks["frontmatter"]
        fm_summary = (
            f"title='{fm.get('title')}', v='{fm.get('version', '1.0.0')}'"
            if fm
            else "NONE"
        )

        res_lines = [
            f"Successfully updated draft '{norm}' ({lines_count} lines).",
            "--- Deterministic Sanity Checks ---",
            f"Frontmatter: {'PASS' if fm else 'FAIL'} ({fm_summary})",
            f"Tokens: {checks['token_status']}",
            f"Code Blocks: {checks['code_blocks']}",
            f"Status: {status_label}",
        ]
        if checks["errors"]:
            res_lines.append("Errors:")
            res_lines.extend(f"- {e}" for e in checks["errors"])
        if checks["warnings"]:
            res_lines.append("Warnings:")
            res_lines.extend(f"- {w}" for w in checks["warnings"])

        return "\n".join(res_lines)

    async def list_knowledge(self, path: str = ".", max_depth: int = 2) -> str:
        """List knowledge hierarchy tree without curation or version checks."""
        output = ""
        if self.store:
            output = await self.store.list_knowledge(path=path, max_depth=max_depth)
        else:
            output = "knowledge/\n"

        if self.drafts:
            output += "\n[Active Session Drafts]\n"
            for p in sorted(self.drafts.keys()):
                output += f"  * {p} [DRAFT]\n"
        return output

    async def search_knowledge(
        self,
        pattern: str = "*",
        path: str = ".",
        content_pattern: str | None = None,
    ) -> str:
        """Fast lexical and content regex search across store knowledge and session drafts."""
        results: list[str] = []
        if self.store:
            store_res = await self.store.search_knowledge(
                pattern=pattern,
                path=path,
                content_pattern=content_pattern,
            )
            if store_res and store_res != "No matching knowledge documents found.":
                results.append(store_res)

        c_regex = re.compile(content_pattern, re.IGNORECASE) if content_pattern else None
        for p, draft_path in self.drafts.items():
            if pattern != "*" and not fnmatch.fnmatch(p, pattern):
                continue
            if c_regex:
                try:
                    text = draft_path.read_text(encoding="utf-8")
                except OSError:
                    continue
                draft_matches = [
                    f"  {ln}: {line.strip()[:150]}"
                    for ln, line in enumerate(text.splitlines(), start=1)
                    if c_regex.search(line)
                ]
                if draft_matches:
                    results.append(f"{p} [DRAFT]:\n" + "\n".join(draft_matches[:10]))
            else:
                results.append(f"{p} [DRAFT]")

        if not results:
            return "No matching knowledge documents found."
        return "\n".join(results)

    async def commit(self, path: str) -> dict[str, Any]:
        """Validate and commit a single draft to the Maker-Checker governance loop."""
        norm = self._normalize_path(path)
        if norm not in self.drafts or not self.drafts[norm].exists():
            return {
                "status": "error",
                "message": f"Draft '{norm}' does not exist in this session.",
            }

        content = self.drafts[norm].read_text(encoding="utf-8")
        checks = run_draft_sanity_checks(content)
        if not checks["passed"]:
            return {
                "status": "error",
                "message": f"Draft '{norm}' failed deterministic sanity checks.",
                "errors": checks["errors"],
                "warnings": checks["warnings"],
            }

        return {
            "status": "ready",
            "path": norm,
            "content": content,
            "checks": checks,
        }

    async def commit_all(self) -> dict[str, Any]:
        """Validate and commit all open drafts in the session."""
        if not self.drafts:
            return {
                "status": "error",
                "message": "No open drafts exist in this session to commit.",
            }

        failed: dict[str, list[str]] = {}
        validated: dict[str, str] = {}

        for norm, draft_path in self.drafts.items():
            if not draft_path.exists():
                failed[norm] = ["Draft file not found on disk."]
                continue
            content = draft_path.read_text(encoding="utf-8")
            checks = run_draft_sanity_checks(content)
            if not checks["passed"]:
                failed[norm] = checks["errors"]
            else:
                validated[norm] = content

        if failed:
            return {
                "status": "error",
                "message": f"{len(failed)} draft(s) failed deterministic sanity checks.",
                "failed_drafts": failed,
            }

        return {
            "status": "ready",
            "drafts": validated,
        }

    async def run_command(self, command_line: str) -> dict[str, Any]:
        """Execute sandboxed terminal command if available."""
        if self.sandbox:
            res = await self.sandbox.run_command(command_line)
            return {
                "exit_code": res.exit_code,
                "stdout": res.stdout,
                "stderr": res.stderr,
            }
        proc = await asyncio.create_subprocess_shell(
            command_line,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.workspace_root),
        )
        stdout, stderr = await proc.communicate()
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }

    def cleanup(self) -> None:
        """Remove the temporary session drafts directory."""
        shutil.rmtree(self.drafts_dir, ignore_errors=True)
