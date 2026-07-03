"""Build one mir-emulator wheel per tracked MiR version.

Each wheel's package version equals the MiR software version it emulates by
default, so ``pip install mir-emulator==3.5.4`` gets a 3.5.4 robot. Run from
the repo root:

    uv run python scripts/build_versioned.py [--out dist]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PACKAGE_DIR = Path("packages/mir-emulator")
VERSION_FILE = PACKAGE_DIR / "src/mir_emulator/_version.py"
REGISTRY = PACKAGE_DIR / "src/mir_emulator/specs/registry.json"

STAMP_TEMPLATE = """# Stamped by scripts/build_versioned.py — do not commit this form.
__version__ = "{version}"

DEFAULT_MIR_VERSION: str | None = "{version}"
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("dist"))
    args = parser.parse_args()

    if not REGISTRY.is_file():
        print("run from the repo root", file=sys.stderr)
        return 2

    versions = [t["mir_version"] for t in json.loads(REGISTRY.read_text())["tracked"]]
    original = VERSION_FILE.read_text()
    built: list[str] = []
    try:
        for version in versions:
            VERSION_FILE.write_text(STAMP_TEMPLATE.format(version=version))
            subprocess.run(  # noqa: S603 - fixed argv
                ["uv", "build", "--package", "mir-emulator", "--out-dir", str(args.out)],  # noqa: S607 - uv from PATH by design
                check=True,
            )
            built.append(version)
    finally:
        VERSION_FILE.write_text(original)

    print(f"built mir-emulator wheels for MiR versions: {', '.join(built)} -> {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
