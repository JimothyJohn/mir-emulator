"""Access to the bundled spec registry (specs/registry.json)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


@dataclass(frozen=True)
class TrackedSpec:
    mir_version: str
    format: str
    file: str
    sha256: str
    official: bool
    provenance: str


def _specs_dir() -> Path:
    return Path(str(resources.files("mir_emulator").joinpath("specs")))


def load_registry() -> dict:
    return json.loads((_specs_dir() / "registry.json").read_text())


def tracked_specs() -> list[TrackedSpec]:
    return [
        TrackedSpec(
            mir_version=t["mir_version"],
            format=t["format"],
            file=t["file"],
            sha256=t["sha256"],
            official=t["official"],
            provenance=t["provenance"],
        )
        for t in load_registry()["tracked"]
    ]


def version_key(version: str) -> tuple[int, ...]:
    """Sort key for dotted numeric versions, tolerating 4-part MiR versions."""
    return tuple(int(p) for p in version.split("."))


def supported_versions() -> list[str]:
    """Tracked MiR versions, newest first."""
    return sorted((t.mir_version for t in tracked_specs()), key=version_key, reverse=True)


def spec_path(mir_version: str | None = None) -> tuple[str, Path]:
    """Resolve a MiR version (or the default) to its bundled spec file.

    Returns (resolved_version, path). Raises KeyError for unknown versions.
    """
    specs = {t.mir_version: t for t in tracked_specs()}
    if mir_version is None:
        from mir_emulator._version import DEFAULT_MIR_VERSION

        mir_version = DEFAULT_MIR_VERSION or supported_versions()[0]
    if mir_version not in specs:
        raise KeyError(
            f"MiR version {mir_version!r} is not tracked; available: {supported_versions()}"
        )
    return mir_version, _specs_dir() / specs[mir_version].file
