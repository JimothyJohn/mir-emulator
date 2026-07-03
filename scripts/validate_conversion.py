"""Structurally compare a converted MiR spec against a reference swagger doc.

    uv run python scripts/validate_conversion.py CONVERTED.json REFERENCE.json

Thin CLI over mir_spec_scraper.validate.compare — exit 0 iff structurally
equivalent (descriptions ignored).
"""

from __future__ import annotations

import json
import sys

from mir_spec_scraper.validate import compare


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    with open(sys.argv[1]) as fh:
        converted = json.load(fh)
    with open(sys.argv[2]) as fh:
        reference = json.load(fh)
    problems = compare(converted, reference)
    for problem in problems[:60]:
        print(problem)
    if len(problems) > 60:
        print(f"... and {len(problems) - 60} more")
    print(f"{len(problems)} structural differences")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
