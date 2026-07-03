"""Version parsing and the tracking-selection rule.

Rule: track the latest minor.patch of each of the newest N (default 3) major
versions that publish REST API files. If fewer than N majors exist, fill the
remaining slots with the previous minor lines of the newest major, so N
distinct API generations are always covered when the portal offers them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# Lookarounds instead of \b: versions are often embedded in filenames like
# mir_rest_api_2.13.3.1.zip, where "_2" has no word boundary and a naive \b
# regex latches onto "13.3.1".
VERSION_RE = re.compile(r"(?<![\d.])(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?(?!\.?\d)")


def parse_version(text: str) -> tuple[int, ...] | None:
    """Extract the first dotted version (3 or 4 parts) from *text*."""
    match = VERSION_RE.search(text)
    if not match:
        return None
    return tuple(int(g) for g in match.groups() if g is not None)


def format_version(version: tuple[int, ...]) -> str:
    return ".".join(str(p) for p in version)


def select_tracked(versions: Sequence[tuple[int, ...]], majors: int = 3) -> list[tuple[int, ...]]:
    """Pick which of *versions* to track, newest first."""
    unique = sorted(set(versions), reverse=True)
    if not unique:
        return []

    latest_per_major: dict[int, tuple[int, ...]] = {}
    for v in unique:
        latest_per_major.setdefault(v[0], v)
    picked = [latest_per_major[m] for m in sorted(latest_per_major, reverse=True)[:majors]]

    if len(picked) < majors:
        newest_major = unique[0][0]
        latest_per_minor: dict[int, tuple[int, ...]] = {}
        for v in unique:
            if v[0] == newest_major:
                latest_per_minor.setdefault(v[1], v)
        for minor in sorted(latest_per_minor, reverse=True):
            candidate = latest_per_minor[minor]
            if candidate not in picked:
                picked.append(candidate)
            if len(picked) >= majors:
                break

    return sorted(picked, reverse=True)
