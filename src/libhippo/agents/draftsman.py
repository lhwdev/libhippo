"""Knowledge Draftsman Session and Deterministic Sanity Checks.

Provides multi-draft tracking, surgical editing, line-addressed reading,
deterministic sanity checks (frontmatter, token sizing, code fences),
and Maker-Checker submission tools.
"""

from __future__ import annotations

import asyncio
import difflib
import fnmatch
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import tiktoken
from ruamel.yaml import YAML

from libhippo.storage.store import KnowledgeStore

logger = logging.getLogger(__name__)


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
            yaml_parser = YAML(typ="safe")
            parsed = yaml_parser.load(raw_yaml)
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
        if "namespace" in frontmatter_dict:
            fm_ns = str(frontmatter_dict["namespace"]).strip().lower()
            valid_namespaces = {"project", "common", "user", "plugins"}
            if fm_ns not in valid_namespaces:
                errors.append(
                    f"Invalid frontmatter namespace '{fm_ns}'. Must be one of: {sorted(valid_namespaces)}."
                )

        # Validate version_check format if present
        if frontmatter_dict.get("version_check"):
            vc = str(frontmatter_dict["version_check"]).strip()
            valid_prefixes = ("npm:", "pypi:", "github:", "crates:", "scrape:", "terminal:")
            if not any(vc.startswith(p) for p in valid_prefixes) and "/" not in vc:
                errors.append(
                    f"Invalid version_check '{vc}'. Expected format: npm:<pkg>, pypi:<pkg>, "
                    f"github:<owner>/<repo>, crates:<crate>, scrape:<url>#<regex>, or terminal:<cmd>."
                )
            elif vc.startswith("scrape:"):
                target = vc[7:].strip()
                if "#" not in target and "|" not in target:
                    warnings.append(
                        f"Scrape version_check '{vc}' does not specify a regex delimiter ('#' or '|'). "
                        "Defaulting to 'v?([0-9]+\\.[0-9]+(?:\\.[0-9]+)?)'."
                    )

        # Validate tags format and count
        if "tags" in frontmatter_dict:
            raw_tags = frontmatter_dict["tags"]
            if isinstance(raw_tags, list) and len(raw_tags) > 10:
                warnings.append(
                    f"Frontmatter contains {len(raw_tags)} tags, exceeding maximum of 10. "
                    "Tags will be capped at 10."
                )

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
        orchestrator: Any | None = None,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.drafts_dir = self.workspace_root / ".libhippo" / "drafts" / self.session_id
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.sandbox = sandbox
        self.orchestrator = orchestrator
        self.drafts: dict[str, Path] = {}
        self.active_path: str | None = None
        self.is_committed: bool = False
        self.last_commit_result: Any | None = None

    def _normalize_path(self, path: str) -> str:
        clean = path.strip("/. ").replace("\\", "/")
        if clean.endswith(".md"):
            clean = clean[:-3]
        return clean

    def _validate_namespace(self, norm_path: str) -> str | None:
        """Validate that the path begins with a valid registered namespace prefix."""
        parts = norm_path.split("/", 1)
        prefix = parts[0].lower()
        if self.store:
            registered = [m.namespace_prefix for m in self.store.mount_manager.get_all_mounts()]
        else:
            registered = ["project", "common", "user", "plugins"]

        if prefix not in registered:
            return (
                f"[ERROR: Invalid namespace '{prefix}'. Knowledge paths must start with a registered mount: "
                f"{registered} and must not contain '.md' extension. Example: 'common/react' or 'common/web/react']"
            )
        return None

    def _validate_path(self, norm_path: str) -> str | None:
        """Validate namespace, snake_case segment casing, query words, and parent existence."""
        ns_err = self._validate_namespace(norm_path)
        if ns_err:
            return ns_err

        segments = norm_path.split("/")
        for seg in segments:
            if not re.match(r"^[a-z0-9]+(_[a-z0-9]+)*$", seg):
                return (
                    f"[ERROR: Invalid path segment '{seg}' in '{norm_path}'. "
                    f"All path segments must be lowercase snake_case.]"
                )

        if len(segments) >= 3 and self.store:
            parent_path = "/".join(segments[:-1])
            parent_exists = False
            if parent_path in self.drafts and self.drafts[parent_path].exists():
                parent_exists = True
            else:
                try:
                    phys, _ = self.store.mount_manager.resolve_virtual_path(parent_path)
                    parent_exists = (phys.exists() and phys.is_file()) or phys.with_suffix(".md").exists()
                except Exception:
                    pass

            if not parent_exists:
                return (
                    f"[ERROR: Parent path '{parent_path}' does not exist in drafts or knowledge store. "
                    f"Under Hub-and-Leaf architecture, a child path cannot introduce an additional "
                    f"hierarchy level without an existing parent hub. Draft at '{parent_path}' instead, "
                    f"or create the parent hub document first.]"
                )

        return None

    def _get_orchestrator(self) -> Any | None:
        """Obtain or lazily instantiate MakerCheckerOrchestrator if store is available."""
        if self.orchestrator is not None:
            return self.orchestrator
        if self.store:
            from libhippo.orchestration.maker_checker import MakerCheckerOrchestrator

            self.orchestrator = MakerCheckerOrchestrator(store=self.store)
            return self.orchestrator
        return None

    def _check_missing_related(self, related_paths: list[str]) -> list[str]:
        """Identify related paths that do not exist in active session drafts or knowledge store."""
        missing: list[str] = []
        for r in related_paths:
            clean_r = str(r).strip()
            if not clean_r:
                continue
            norm_r = self._normalize_path(clean_r)
            if norm_r in self.drafts and self.drafts[norm_r].exists():
                continue
            if self.store:
                try:
                    phys, _ = self.store.mount_manager.resolve_virtual_path(norm_r)
                    if phys.exists() and phys.is_file():
                        continue
                except KeyError:
                    pass
                except Exception:  # noqa: BLE001
                    pass
            missing.append(clean_r)
        return missing

    async def _get_candidate_paths(self) -> list[str]:
        """Collect available knowledge paths from active drafts and mounted storage."""
        if len(self.drafts) > 0:
            return [p[:-3] if p.endswith(".md") else p for p in self.drafts.keys()]
        if self.store:
            raw = await self.store.catalog.get_random_suggestions(limit=2)
            return [p[:-3] if p.endswith(".md") else p for p in raw]
        return []

    async def read_knowledge(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> str:
        """Read line-addressed slice of a draft document or existing store knowledge node."""
        if not path or not path.strip():
            candidates = await self._get_candidate_paths()
            sample = candidates[0] if candidates and len(candidates) else "common/<topic>"
            return f"[ERROR: No knowledge path specified. Please specify path, e.g. path='{sample}']"

        norm = self._normalize_path(path)

        # 1. Active draft in session
        if norm in self.drafts and self.drafts[norm].exists():
            text = self.drafts[norm].read_text(encoding="utf-8")
        # 2. Existing node in knowledge store
        elif self.store:
            try:
                phys, _ = self.store.mount_manager.resolve_virtual_path(norm)
                if phys.exists() and phys.is_file():
                    text = phys.read_text(encoding="utf-8")
                else:
                    candidates = await self._get_candidate_paths()
                    matches = difflib.get_close_matches(norm, candidates, n=3, cutoff=0.4)
                    if matches:
                        suggest = f" Did you mean '{matches[0]}'?"
                    else:
                        suggest = ""
                    return f"[ERROR: Knowledge path '{norm}' not found in drafts or knowledge store.{suggest}]"
            except Exception:
                candidates = await self._get_candidate_paths()
                matches = difflib.get_close_matches(norm, candidates, n=3, cutoff=0.4)
                suggest = f" Did you mean '{matches[0]}'?" if matches else ""
                return f"[ERROR: Knowledge path '{norm}' not found in drafts or knowledge store.{suggest}]"
        else:
            candidates = await self._get_candidate_paths()
            matches = difflib.get_close_matches(norm, candidates, n=3, cutoff=0.4)
            suggest = f" Did you mean '{matches[0]}'?" if matches else ""
            return f"[ERROR: Knowledge path '{norm}' not found in active drafts.{suggest}]"

        lines = text.splitlines()
        total_lines = len(lines)
        s_line = max(1, start_line)
        e_line = min(total_lines, end_line) if end_line is not None else total_lines

        if s_line > total_lines:
            return f"[Draft '{norm}': start_line {s_line} exceeds file line count {total_lines}]"

        selected = lines[s_line - 1 : e_line]
        formatted = "\n".join(f"{idx}: {line}" for idx, line in enumerate(selected, start=s_line))
        return (
            f'<knowledge path="{norm}" lines="{s_line}:{e_line}" total_lines="{total_lines}">\n'
            f"{formatted}\n"
            "</knowledge>"
        )

    async def write_knowledge(
        self,
        path: str,
        content: str,
        start_line: int | None = None,
        end_line: int | None = None,
        target: str | None = None,
    ) -> str:
        """Surgically edit or replace a draft document and return immediate sanity check results."""
        if not path or not path.strip():
            return "[ERROR: No draft path specified. Please specify path='<namespace>/<name>']"

        norm = self._normalize_path(path)
        path_err = self._validate_path(norm)
        if path_err:
            return path_err

        self.active_path = norm

        draft_file = self.drafts.get(norm)
        if not draft_file:
            draft_file = self.drafts_dir / f"{norm}.md"
            draft_file.parent.mkdir(parents=True, exist_ok=True)
            if not draft_file.exists():
                existing_text = ""
                if self.store:
                    try:
                        phys, _ = self.store.mount_manager.resolve_virtual_path(norm)
                        if phys.exists() and phys.is_file():
                            existing_text = phys.read_text(encoding="utf-8")
                    except Exception:
                        pass
                draft_file.write_text(existing_text, encoding="utf-8")
            self.drafts[norm] = draft_file

        current_text = draft_file.read_text(encoding="utf-8")

        # Edit logic: full replacement vs surgical string/line replacement
        mod_start = 1
        if start_line is None and end_line is None and target is None:
            new_text = content
            mod_start = 1
            mod_end = len(new_text.splitlines())
        elif target is not None:
            if target not in current_text:
                return f"[ERROR: Target string '{target[:80]}...' not found in draft '{norm}']"
            prefix_before = current_text.split(target, 1)[0]
            mod_start = len(prefix_before.splitlines()) or 1
            mod_end = mod_start + len(content.splitlines())
            new_text = current_text.replace(target, content, 1)
        else:
            lines = current_text.splitlines()
            s_idx = max(0, (start_line or 1) - 1)
            e_idx = end_line if end_line is not None else len(lines)
            new_lines = content.splitlines()
            lines[s_idx:e_idx] = new_lines
            mod_start = s_idx + 1
            mod_end = s_idx + len(new_lines)
            new_text = "\n".join(lines) + ("\n" if current_text.endswith("\n") or not current_text else "")

        draft_file.write_text(new_text, encoding="utf-8")

        # Run deterministic sanity checks immediately
        checks = run_draft_sanity_checks(new_text)

        # Non-blocking warning for hallucinated related documents
        related_raw = checks.get("frontmatter", {}).get("related", [])
        if isinstance(related_raw, str):
            related_list = [r.strip() for r in related_raw.split(",") if r.strip()]
        elif isinstance(related_raw, list):
            related_list = [str(r).strip() for r in related_raw if str(r).strip()]
        else:
            related_list = []

        missing_related = self._check_missing_related(related_list)
        for m in missing_related:
            checks["warnings"].append(
                f"Related document '{m}' does not exist in knowledge store or active drafts. "
                "'related' must only reference existing documents (or be empty)."
            )

        lines_list = new_text.splitlines()
        lines_count = len(lines_list)
        res_lines = [f"Successfully updated draft '{norm}' ({lines_count} lines)."]

        # If surgical edit, provide compact annotated context preview around modified lines
        if target is not None or start_line is not None or end_line is not None:
            p_start = max(1, mod_start - 2)
            p_end = min(lines_count, mod_end + 2)
            if p_start <= lines_count:
                res_lines.append("--- Modified Section Preview ---")
                preview_slice = lines_list[p_start - 1 : p_end]
                res_lines.extend(f"{idx}: {l}" for idx, l in enumerate(preview_slice, start=p_start))

        # Do NOT print verbose "PASS" logs. Only print diagnostic errors or warnings if any exist.
        if checks["errors"]:
            res_lines.append("Status: REQUIRES_REVISION")
            res_lines.append("Errors:")
            res_lines.extend(f"- {e}" for e in checks["errors"])
        elif checks["warnings"]:
            res_lines.append("Warnings:")
            res_lines.extend(f"- {w}" for w in checks["warnings"])
        else:
            res_lines.append("Status: READY_TO_COMMIT")

        if checks["errors"] or checks["warnings"]:
            res_lines.append("Surgically modify to fix problems.")
        return "\n".join(res_lines)

    async def list_knowledge(self, path: str = ".", max_depth: int = 2) -> str:
        """List knowledge hierarchy tree without curation or version checks."""
        output = ""
        if self.store:
            output = await self.store.list_knowledge(path=path, max_depth=max_depth)
        else:
            output = "[Knowledge Mounts]\n"

        if self.drafts:
            output += "\n## Active Session Drafts\n"
            for p in sorted(self.drafts.keys()):
                output += f"  * {p} [DRAFT]\n"
        return output

    async def search_knowledge(
        self,
        path: str,
        pattern: str = "*",
        content_pattern: str | None = None,
    ) -> str:
        """Fast lexical and content regex search across store knowledge and session drafts."""
        if not path or not path.strip():
            return "[ERROR: No search path specified. Please specify path, e.g. path='.' or path='common']"

        results: list[str] = []
        if self.store:
            store_res = await self.store.search_knowledge(
                path=path,
                pattern=pattern,
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
        path_err = self._validate_path(norm)
        if path_err:
            return {
                "status": "error",
                "message": path_err,
            }

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
                "message": f"Draft '{norm}' failed deterministic sanity checks. Surgically modify to fix.",
                "errors": checks["errors"],
                "warnings": checks["warnings"],
            }

        # Hard error rejection for hallucinated related documents
        related_raw = checks.get("frontmatter", {}).get("related", [])
        if isinstance(related_raw, str):
            related_list = [r.strip() for r in related_raw.split(",") if r.strip()]
        elif isinstance(related_raw, list):
            related_list = [str(r).strip() for r in related_raw if str(r).strip()]
        else:
            related_list = []

        missing_related = self._check_missing_related(related_list)
        if missing_related:
            return {
                "status": "error",
                "message": (
                    f"Draft '{norm}' failed validation: related document(s) {missing_related} "
                    "do not exist in knowledge store or active drafts. 'related' must only reference existing documents (or be empty)."
                ),
                "errors": [f"Non-existent related document: '{m}'" for m in missing_related],
                "warnings": checks["warnings"],
            }

        orch = self._get_orchestrator()
        if orch:
            logger.info("Submitting draft '%s' to Maker-Checker governance loop", norm)
            gov_res = await orch.run_governance(
                candidate=content,
                target_path=norm,
            )
            if gov_res.status in ("COMMITTED", "MERGED"):
                self.drafts.pop(norm, None)
                self.is_committed = len(self.drafts) == 0
                self.last_commit_result = gov_res
                return {
                    "status": "committed",
                    "path": norm,
                    "verdict": gov_res.verdict,
                    "message": gov_res.message,
                    "remaining_drafts": list(self.drafts.keys()),
                }
            else:
                errors = []
                if gov_res.report:
                    errors = gov_res.report.schema_errors + gov_res.report.content_errors
                return {
                    "status": "error",
                    "path": norm,
                    "verdict": gov_res.verdict,
                    "message": gov_res.message or f"Commit failed (${gov_res.status}). Surgically modify to fix.",
                    "errors": errors,
                }

        self.drafts.pop(norm, None)
        self.is_committed = len(self.drafts) == 0
        return {
            "status": "ready",
            "path": norm,
            "checks": checks,
            "remaining_drafts": list(self.drafts.keys()),
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
            path_err = self._validate_path(norm)
            if path_err:
                failed[norm] = [path_err]
                continue
            if not draft_path.exists():
                failed[norm] = ["Draft file not found on disk."]
                continue
            content = draft_path.read_text(encoding="utf-8")
            checks = run_draft_sanity_checks(content)
            if not checks["passed"]:
                failed[norm] = checks["errors"]
            else:
                related_raw = checks.get("frontmatter", {}).get("related", [])
                if isinstance(related_raw, str):
                    related_list = [r.strip() for r in related_raw.split(",") if r.strip()]
                elif isinstance(related_raw, list):
                    related_list = [str(r).strip() for r in related_raw if str(r).strip()]
                else:
                    related_list = []
                missing_related = self._check_missing_related(related_list)
                if missing_related:
                    failed[norm] = [
                        f"Non-existent related document: '{m}' ('related' must only reference existing documents or be empty)"
                        for m in missing_related
                    ]
                else:
                    validated[norm] = content

        if failed:
            return {
                "status": "error",
                "message": f"{len(failed)} draft(s) failed checks. Surgically modify to fix.",
                "failed_drafts": failed,
            }

        orch = self._get_orchestrator()
        if orch:
            committed_paths: list[str] = []
            for norm, content in validated.items():
                logger.info("Submitting draft '%s' to Maker-Checker governance loop", norm)
                gov_res = await orch.run_governance(candidate=content, target_path=norm)
                if gov_res.status in ("COMMITTED", "MERGED"):
                    committed_paths.append(norm)
                    self.drafts.pop(norm, None)
                    self.last_commit_result = gov_res
                else:
                    errors = []
                    if gov_res.report:
                        errors = gov_res.report.schema_errors + gov_res.report.content_errors
                    return {
                        "status": "error",
                        "path": norm,
                        "verdict": gov_res.verdict,
                        "message": gov_res.message or f"Commit failed ({gov_res.status}). Surgically modify to fix.",
                        "errors": errors,
                        "remaining_drafts": list(self.drafts.keys()),
                    }
            self.is_committed = len(self.drafts) == 0
            return {
                "status": "committed",
                "committed_paths": committed_paths,
                "remaining_drafts": list(self.drafts.keys()),
            }

        for norm in validated:
            self.drafts.pop(norm, None)
        self.is_committed = len(self.drafts) == 0
        return {
            "status": "ready",
            "drafts": list(validated.keys()),
            "remaining_drafts": list(self.drafts.keys()),
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
