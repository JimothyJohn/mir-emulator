"""mir-emulator: local, spec-faithful emulator of the MiR robot REST API."""

from mir_emulator._version import __version__
from mir_emulator.app import create_app
from mir_emulator.registry import supported_versions

__all__ = ["__version__", "create_app", "supported_versions"]
