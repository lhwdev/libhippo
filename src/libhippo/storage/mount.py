"""Dynamic Namespace Mounts for LibHippo.

Enables mapping virtual knowledge namespaces (project/, common/, user/, plugins/)
to distinct physical directories with per-mount read/write permissions.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class ReadOnlyMountError(PermissionError):
    """Raised when an operation attempts to write to a read-only knowledge mount."""


class MountConfig(BaseModel):
    """Configuration for a physical directory mounted to a virtual namespace prefix."""

    namespace_prefix: str
    physical_path: Path
    read_only: bool = False

    model_config = {"arbitrary_types_allowed": True}


class MountManager:
    """Manages namespace mount points and translates virtual paths to physical paths."""

    def __init__(
        self,
        mounts: list[MountConfig] | None = None,
        fallback_root: Path | None = None,
    ) -> None:
        self._mounts: dict[str, MountConfig] = {}
        self.fallback_root = Path(fallback_root) if fallback_root else None
        if mounts:
            for mount in mounts:
                self.register_mount(mount)

    def register_mount(self, mount: MountConfig) -> None:
        """Register a mount point for a namespace prefix."""
        prefix = mount.namespace_prefix.strip("/").lower()
        self._mounts[prefix] = mount
        try:
            mount.physical_path.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError):
            pass

    def get_mount(self, namespace_prefix: str) -> MountConfig | None:
        """Retrieve MountConfig for a given prefix."""
        prefix = namespace_prefix.strip("/").lower()
        return self._mounts.get(prefix)

    def get_all_mounts(self) -> list[MountConfig]:
        """Return all registered mount configurations."""
        return list(self._mounts.values())

    def resolve_virtual_path(self, virtual_path: str) -> tuple[Path, MountConfig]:
        """Resolve a virtual namespaced path to a physical filesystem path and its MountConfig.

        Example:
            "common/web/html.md" -> (Path("/path/to/common/web/html.md"), MountConfig(prefix="common", ...))
        """
        clean = virtual_path.lstrip("/").replace("\\", "/")
        if not clean.endswith(".md"):
            clean = f"{clean}.md"

        parts = clean.split("/", 1)
        prefix = parts[0].lower()
        subpath = parts[1] if len(parts) > 1 else ""

        if prefix in self._mounts:
            mount = self._mounts[prefix]
            target_path = mount.physical_path / subpath if subpath else mount.physical_path / f"{prefix}.md"
            return target_path, mount

        if self.fallback_root:
            mount = MountConfig(
                namespace_prefix=prefix,
                physical_path=self.fallback_root / prefix,
                read_only=False,
            )
            self.register_mount(mount)
            target_path = mount.physical_path / subpath if subpath else mount.physical_path / f"{prefix}.md"
            return target_path, mount

        # Fallback to root or default mount if available
        if "" in self._mounts:
            mount = self._mounts[""]
            return mount.physical_path / clean, mount

        raise KeyError(
            f"No mount found for namespace '{prefix}'. Registered mounts: {list(self._mounts.keys())}"
        )

    def resolve_physical_path(self, physical_path: Path) -> str | None:
        """Translate a physical filesystem path back to its virtual namespaced path."""
        resolved = physical_path.resolve()
        for prefix, mount in self._mounts.items():
            if not prefix:
                continue
            mount_phys = mount.physical_path.resolve()
            try:
                rel = resolved.relative_to(mount_phys).as_posix()
                return f"{prefix}/{rel}" if rel and rel != "." else f"{prefix}.md"
            except ValueError:
                continue

        if "" in self._mounts:
            mount_phys = self._mounts[""].physical_path.resolve()
            try:
                return resolved.relative_to(mount_phys).as_posix()
            except ValueError:
                pass

        if self.fallback_root:
            fb_phys = self.fallback_root.resolve()
            try:
                return resolved.relative_to(fb_phys).as_posix()
            except ValueError:
                pass

        return None

    def check_writable(self, virtual_path: str) -> MountConfig:
        """Verify that the mount backing virtual_path is writable, raising ReadOnlyMountError if not."""
        _, mount = self.resolve_virtual_path(virtual_path)
        if mount.read_only:
            raise ReadOnlyMountError(
                f"Mount '{mount.namespace_prefix}' at '{mount.physical_path}' is read-only. "
                f"Cannot mutate '{virtual_path}'. Draft overriding knowledge under 'project/' instead."
            )
        return mount


def create_default_mounts(
    workspace_root: Path | None = None,
    install_root: Path | None = None,
    user_root: Path | None = None,
) -> MountManager:
    """Create a standard MountManager with default locations for LibHippo scopes."""
    ws = (workspace_root or Path.cwd()).resolve()
    inst = (install_root or (Path(__file__).parent.parent.parent.parent / "knowledge")).resolve()
    usr = (user_root or (Path.home() / ".config" / "libhippo" / "knowledge")).resolve()

    return MountManager([
        MountConfig(namespace_prefix="project", physical_path=ws / ".libhippo" / "knowledge", read_only=False),
        MountConfig(namespace_prefix="common", physical_path=inst / "common", read_only=False),
        MountConfig(namespace_prefix="user", physical_path=usr, read_only=False),
        MountConfig(namespace_prefix="plugins", physical_path=ws / ".libhippo" / "plugins", read_only=False),
    ])
