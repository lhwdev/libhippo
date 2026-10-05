"""Automated deterministic freshness and staleness checker for LibHippo knowledge nodes."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import httpx
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from libhippo.storage.store import KnowledgeStore

logger = logging.getLogger(__name__)

FreshnessStatus = Literal["fresh", "stale", "error", "skipped"]


@dataclass
class FreshnessResult:
    """Result of evaluating upstream version against local knowledge node."""

    path: str
    status: FreshnessStatus
    current_version: str | None = None
    upstream_version: str | None = None
    provider: str = "none"
    message: str = ""
    is_bump_major: bool = False
    is_bump_minor: bool = False


class FreshnessChecker:
    """Zero-LLM deterministic version checking engine for library and specification nodes."""

    def __init__(
        self,
        timeout: float = 8.0,
        default_interval_seconds: float = 604800.0,  # 7 days
    ) -> None:
        self.timeout = timeout
        self.default_interval_seconds = default_interval_seconds

    async def fetch_pypi_version(self, pkg: str, client: httpx.AsyncClient) -> str | None:
        """Fetch latest version from PyPI JSON API."""
        url = f"https://pypi.org/pypi/{pkg}/json"
        resp = await client.get(url, timeout=self.timeout)
        if resp.status_code == 200:
            data = resp.json()
            return str(data.get("info", {}).get("version", "")).strip() or None
        return None

    async def fetch_npm_version(self, pkg: str, client: httpx.AsyncClient) -> str | None:
        """Fetch latest version from npm registry."""
        url = f"https://registry.npmjs.org/{pkg}/latest"
        resp = await client.get(url, timeout=self.timeout)
        if resp.status_code == 200:
            data = resp.json()
            return str(data.get("version", "")).strip() or None
        return None

    async def fetch_github_version(self, repo: str, client: httpx.AsyncClient) -> str | None:
        """Fetch latest release tag from GitHub API."""
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "LibHippo-Freshness/1.0"}
        resp = await client.get(url, headers=headers, timeout=self.timeout)
        if resp.status_code == 200:
            data = resp.json()
            tag = str(data.get("tag_name", "")).strip()
            return tag.lstrip("vV") or None
        return None

    async def fetch_crates_version(self, crate: str, client: httpx.AsyncClient) -> str | None:
        """Fetch latest version from crates.io API."""
        url = f"https://crates.io/api/v1/crates/{crate}"
        headers = {"User-Agent": "LibHippo-Freshness/1.0 (info@libhippo.dev)"}
        resp = await client.get(url, headers=headers, timeout=self.timeout)
        if resp.status_code == 200:
            data = resp.json()
            return str(data.get("crate", {}).get("max_version", "")).strip() or None
        return None

    async def fetch_scrape_version(self, url: str, pattern: str, client: httpx.AsyncClient) -> str | None:
        """Fetch HTML/JSON and extract version matching regex pattern or JSON key."""
        headers = {"User-Agent": "Mozilla/5.0 (compatible; LibHippo/1.0)"}
        resp = await client.get(url, headers=headers, timeout=self.timeout, follow_redirects=True)
        if resp.status_code == 200:
            content_type = resp.headers.get("content-type", "")
            if "json" in content_type:
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        if "version" in data:
                            return str(data["version"]).strip()
                        if "tag_name" in data:
                            return str(data["tag_name"]).lstrip("vV").strip()
                except Exception:
                    pass
            match = re.search(pattern, resp.text)
            if match:
                return match.group(1).strip()
        return None

    async def fetch_terminal_version(self, command: str) -> str | None:
        """Execute sandboxed shell command and parse version string from stdout."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            out_str = stdout.decode("utf-8").strip()
            match = re.search(r"\b(\d+\.\d+(?:\.\d+)?(?:[a-zA-Z0-9_\-\.]+)?)\b", out_str)
            return match.group(1) if match else (out_str.split()[-1] if out_str else None)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Terminal version check command failed ('{command}'): {e}")
            return None

    def infer_check_spec(self, version_check: str | None, sources: list[str]) -> tuple[str, str] | None:
        """Parse explicit version_check or infer from canonical sources.

        Returns (provider, target) or None.
        """
        if version_check:
            vc = version_check.strip()
            if vc.startswith("npm:"):
                return "npm", vc[4:].strip()
            elif vc.startswith("pypi:"):
                return "pypi", vc[5:].strip()
            elif vc.startswith("github:"):
                return "github", vc[7:].strip()
            elif vc.startswith("crates:"):
                return "crates", vc[7:].strip()
            elif vc.startswith("terminal:"):
                return "terminal", vc[9:].strip()
            elif vc.startswith("scrape:"):
                rest = vc[7:].strip()
                if "#" in rest:
                    url, pat = rest.split("#", 1)
                    return "scrape", f"{url}#{pat}"
                elif "|" in rest:
                    url, pat = rest.split("|", 1)
                    return "scrape", f"{url}#{pat}"
                return "scrape", f"{rest}#v?([0-9]+\\.[0-9]+(?:\\.[0-9]+)?)"
            elif vc.startswith("http://") or vc.startswith("https://"):
                gh_m = re.search(r"github\.com/([a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+)", vc)
                if gh_m:
                    return "github", gh_m.group(1)
                pypi_m = re.search(r"pypi\.org/(?:project|pypi)/([a-zA-Z0-9_\-]+)", vc)
                if pypi_m:
                    return "pypi", pypi_m.group(1)
                npm_m = re.search(r"(?:npmjs\.com/package/|registry\.npmjs\.org/)([a-zA-Z0-9_\-]+)", vc)
                if npm_m:
                    return "npm", npm_m.group(1)
                crates_m = re.search(r"crates\.io/crates/([a-zA-Z0-9_\-]+)", vc)
                if crates_m:
                    return "crates", crates_m.group(1)
                if "#" in vc:
                    url, pat = vc.split("#", 1)
                    return "scrape", f"{url}#{pat}"
                elif "|" in vc:
                    url, pat = vc.split("|", 1)
                    return "scrape", f"{url}#{pat}"
                return "scrape", f"{vc}#v?([0-9]+\\.[0-9]+(?:\\.[0-9]+)?)"
            elif "/" in vc and not vc.startswith("http"):
                return "github", vc

        # Fallback: Infer from sources
        for src in sources:
            gh_m = re.search(r"github\.com/([a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+)", src)
            if gh_m:
                return "github", gh_m.group(1)
            pypi_m = re.search(r"pypi\.org/project/([a-zA-Z0-9_\-]+)", src)
            if pypi_m:
                return "pypi", pypi_m.group(1)
            npm_m = re.search(r"npmjs\.com/package/([a-zA-Z0-9_\-]+)", src)
            if npm_m:
                return "npm", npm_m.group(1)
            crates_m = re.search(r"crates\.io/crates/([a-zA-Z0-9_\-]+)", src)
            if crates_m:
                return "crates", crates_m.group(1)

        return None

    async def fetch_upstream_version(
        self,
        version_check: str | None,
        sources: list[str] | None = None,
    ) -> tuple[str | None, str | None]:
        """Fetch upstream version for a given version_check or sources.

        Returns (upstream_version, provider).
        """
        spec = self.infer_check_spec(version_check, sources or [])
        if not spec:
            return None, None

        provider, target = spec
        upstream: str | None = None
        try:
            if provider == "terminal":
                upstream = await self.fetch_terminal_version(target)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    if provider == "pypi":
                        upstream = await self.fetch_pypi_version(target, client)
                    elif provider == "npm":
                        upstream = await self.fetch_npm_version(target, client)
                    elif provider == "github":
                        upstream = await self.fetch_github_version(target, client)
                    elif provider == "crates":
                        upstream = await self.fetch_crates_version(target, client)
                    elif provider == "scrape":
                        url, pat = (
                            target.split("#", 1)
                            if "#" in target
                            else (target, r"v?([0-9]+\.[0-9]+(?:\.[0-9]+)?)")
                        )
                        upstream = await self.fetch_scrape_version(url, pat, client)
        except Exception as e:
            logger.warning(f"Freshness check network failure for '{provider}:{target}': {e}")
            return None, provider

        return upstream, provider

    @staticmethod
    def compare_versions(current_str: str, upstream_str: str) -> tuple[bool, bool, bool]:
        """Compare two version strings.

        Returns (is_stale, is_major_bump, is_minor_bump).
        """
        try:
            cur = Version(current_str)
            up = Version(upstream_str)
            if up > cur:
                is_major = up.major > cur.major
                is_minor = up.major == cur.major and up.minor > cur.minor
                return True, is_major, is_minor
            return False, False, False
        except InvalidVersion:
            # Fallback for unconventional versions
            cur_digits = [int(p) for p in re.findall(r"\d+", current_str)]
            up_digits = [int(p) for p in re.findall(r"\d+", upstream_str)]
            if up_digits > cur_digits:
                is_major = len(up_digits) > 0 and len(cur_digits) > 0 and up_digits[0] > cur_digits[0]
                is_minor = len(up_digits) > 1 and len(cur_digits) > 1 and up_digits[0] == cur_digits[0] and up_digits[1] > cur_digits[1]
                return True, is_major, is_minor
            return False, False, False

    async def check_node_freshness(
        self,
        path: str,
        store: KnowledgeStore,
        force: bool = False,
        interval_seconds: float | None = None,
    ) -> FreshnessResult:
        """Evaluate freshness of a specific node against its upstream version target."""
        entry = await store.catalog.get(path)
        if not entry:
            return FreshnessResult(path=path, status="error", message=f"Node not found in catalog: {path}")

        interval = interval_seconds if interval_seconds is not None else self.default_interval_seconds
        last_checked = entry.get("last_checked_at")

        if not force and last_checked:
            try:
                checked_dt = datetime.fromisoformat(last_checked)
                age = (datetime.now(UTC) - checked_dt).total_seconds()
                if age < interval:
                    return FreshnessResult(
                        path=path,
                        status="skipped",
                        current_version=entry.get("version"),
                        upstream_version=entry.get("upstream_version"),
                        message=f"Freshness check skipped (checked {int(age // 3600)}h ago; within TTL)",
                    )
            except Exception:  # noqa: BLE001
                pass

        spec = self.infer_check_spec(entry.get("version_check"), entry.get("source", []))
        if not spec:
            now_iso = datetime.now(UTC).isoformat()
            await store.catalog.update_freshness(path=path, freshness_status="fresh", last_checked_at=now_iso)
            return FreshnessResult(
                path=path,
                status="fresh",
                current_version=entry.get("version"),
                message="No upstream version check specification found; marked fresh.",
            )

        provider, _ = spec
        upstream, provider = await self.fetch_upstream_version(
            entry.get("version_check"),
            entry.get("source", []),
        )
        if not upstream:
            now_iso = datetime.now(UTC).isoformat()
            await store.catalog.update_freshness(
                path=path,
                freshness_status="error",
                last_checked_at=now_iso,
                stale_reason="check_failed",
            )
            return FreshnessResult(
                path=path,
                status="error",
                provider=provider or "unknown",
                message="Failed or empty upstream version check response.",
            )

        now_iso = datetime.now(UTC).isoformat()
        current_version = entry.get("version", "1.0.0")

        is_stale, is_major, is_minor = self.compare_versions(current_version, upstream)

        if is_stale:
            stale_reason = f"version_bump:{current_version}->{upstream}"
            await store.catalog.update_freshness(
                path=path,
                freshness_status="stale",
                last_checked_at=now_iso,
                upstream_version=upstream,
                stale_reason=stale_reason,
                cascade_children=True,
            )
            return FreshnessResult(
                path=path,
                status="stale",
                current_version=current_version,
                upstream_version=upstream,
                provider=provider,
                is_bump_major=is_major,
                is_bump_minor=is_minor,
                message=f"Outdated: upstream version is {upstream} (local is {current_version})",
            )
        else:
            await store.catalog.update_freshness(
                path=path,
                freshness_status="fresh",
                last_checked_at=now_iso,
                upstream_version=upstream,
                stale_reason=None,
                cascade_children=True,
            )
            return FreshnessResult(
                path=path,
                status="fresh",
                current_version=current_version,
                upstream_version=upstream,
                provider=provider,
                message=f"Up-to-date with upstream {upstream}.",
            )

    async def check_batch_freshness(
        self,
        store: KnowledgeStore,
        limit: int = 3,
        interval_seconds: float | None = None,
    ) -> list[FreshnessResult]:
        """Perform background freshness checks on a batch of eligible nodes."""
        interval = interval_seconds if interval_seconds is not None else self.default_interval_seconds
        candidates = await store.catalog.list_nodes_for_freshness(limit=limit, interval_seconds=interval)
        results: list[FreshnessResult] = []

        for c in candidates:
            res = await self.check_node_freshness(
                path=c["path"],
                store=store,
                force=False,
                interval_seconds=interval,
            )
            results.append(res)

        return results
