"""Sync the bundled specs with the MiR support portal.

Usage (from the repo root):

    MIR_PORTAL_EMAIL=... MIR_PORTAL_PASSWORD=... mir-spec-scraper sync

Exit code is always 0 unless something actually broke; "no credentials" and
"nothing new" are normal outcomes. When run inside GitHub Actions, writes
``changed=true|false`` and ``summary=...`` to $GITHUB_OUTPUT so the workflow
can decide whether to open a PR.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

from mir_spec_scraper.portal import PortalClient, PortalFile
from mir_spec_scraper.versions import format_version, select_tracked

DEFAULT_SPECS_DIR = Path("packages/mir-emulator/src/mir_emulator/specs")


def _github_output(changed: bool, summary: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with open(output_path, "a") as fh:
        fh.write(f"changed={'true' if changed else 'false'}\n")
        fh.write(f"summary={summary}\n")


def _sniff_format(data: bytes) -> tuple[str, str]:
    """Return (registry format string, file extension) for a spec payload."""
    text = data[:2000].decode("utf-8", errors="replace")
    if '"swagger"' in text or "swagger:" in text:
        fmt = "swagger-2.0"
    elif '"openapi"' in text or "openapi:" in text:
        fmt = "openapi-3.0"
    else:
        raise ValueError("downloaded file is neither Swagger 2.0 nor OpenAPI 3.x")
    ext = ".json" if text.lstrip().startswith("{") else ".yaml"
    return fmt, ext


def _extract_spec(data: bytes) -> bytes:
    """Unwrap a zip download to its single spec member, or pass data through."""
    if data[:4] != b"PK\x03\x04":
        return data
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = [
            m
            for m in zf.namelist()
            if m.lower().endswith((".json", ".yaml", ".yml")) and not m.startswith("__MACOSX")
        ]
        if not members:
            raise ValueError("zip download contains no .json/.yaml spec")
        members.sort(key=lambda m: (not m.lower().endswith(".json"), len(m)))
        return zf.read(members[0])


def _pick_file(files: list[PortalFile], version: tuple[int, ...]) -> PortalFile:
    candidates = [f for f in files if f.version == version]
    candidates.sort(key=lambda f: (not f.url.lower().endswith(".json"), f.url))
    return candidates[0]


def sync(specs_dir: Path, majors: int, dry_run: bool) -> tuple[bool, str]:
    email = os.environ.get("MIR_PORTAL_EMAIL", "")
    password = os.environ.get("MIR_PORTAL_PASSWORD", "")
    if not email or not password:
        return False, (
            "skipped: MIR_PORTAL_EMAIL/MIR_PORTAL_PASSWORD not set "
            "(free account: supportportal.mobile-industrial-robots.com)"
        )

    registry_path = specs_dir / "registry.json"
    registry = json.loads(registry_path.read_text())
    existing = {t["mir_version"]: t for t in registry["tracked"]}

    client = PortalClient(email, password)
    try:
        client.login()
        files = client.list_files()
        if not files:
            return False, "portal listing parsed to zero API files; parser may need updating"

        targets = select_tracked([f.version for f in files], majors=majors)
        changed = False
        tracked = []
        lines = []
        for version in targets:
            version_str = format_version(version)
            portal_file = _pick_file(files, version)
            data = _extract_spec(client.download(portal_file))
            fmt, ext = _sniff_format(data)
            sha = hashlib.sha256(data).hexdigest()
            entry = existing.get(version_str)
            if entry and entry["sha256"] == sha:
                tracked.append(entry)
                continue
            rel = f"{version_str}/swagger{ext}"
            if not dry_run:
                target_path = specs_dir / rel
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(data)
            tracked.append(
                {
                    "mir_version": version_str,
                    "api_path_version": "v2.0.0",
                    "format": fmt,
                    "file": rel,
                    "sha256": sha,
                    "official": True,
                    "provenance": f"MiR support portal: {portal_file.url}",
                }
            )
            changed = True
            lines.append(
                f"{'would add' if dry_run else 'added'} {version_str} from {portal_file.url}"
            )
    finally:
        client.close()

    dropped = set(existing) - {t["mir_version"] for t in tracked}
    if dropped:
        changed = True
        lines.append(f"dropped (no longer selected): {', '.join(sorted(dropped))}")

    if changed and not dry_run:
        registry["tracked"] = tracked
        registry["updated"] = datetime.date.today().isoformat()
        registry["wanted"] = []
        registry_path.write_text(json.dumps(registry, indent=2) + "\n")
        for version_str in dropped:
            old = existing[version_str]["file"].split("/")[0]
            shutil.rmtree(specs_dir / old, ignore_errors=True)

    summary = "; ".join(lines) if lines else "no changes: portal matches tracked specs"
    return changed, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mir-spec-scraper")
    sub = parser.add_subparsers(dest="command", required=True)
    p_sync = sub.add_parser("sync", help="fetch portal listing and update bundled specs")
    p_sync.add_argument("--specs-dir", type=Path, default=DEFAULT_SPECS_DIR)
    p_sync.add_argument("--majors", type=int, default=3)
    p_sync.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not (args.specs_dir / "registry.json").is_file():
        print(
            f"error: no registry.json under {args.specs_dir} (run from the repo root?)",
            file=sys.stderr,
        )
        return 2

    changed, summary = sync(args.specs_dir, args.majors, args.dry_run)
    print(summary)
    _github_output(changed, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
