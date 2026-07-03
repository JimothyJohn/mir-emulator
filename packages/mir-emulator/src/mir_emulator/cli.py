"""Run the emulator: ``mir-emulator --mir-version 3.5.4 --port 8080``."""

from __future__ import annotations

import argparse
import os

from mir_emulator import auth, registry
from mir_emulator._version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mir-emulator",
        description="Local emulator for the MiR robot REST API (/api/v2.0.0).",
    )
    parser.add_argument(
        "--mir-version",
        default=None,
        help=f"MiR software version to emulate; one of {registry.supported_versions()}",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--no-auth", action="store_true", help="Disable Authorization header checks"
    )
    parser.add_argument(
        "--cors", action="store_true", help="Allow cross-origin requests (browser dashboards)"
    )
    parser.add_argument(
        "--mission-duration",
        type=float,
        default=10.0,
        help="Seconds a queued mission spends Executing before it is Done (default: 10)",
    )
    parser.add_argument(
        "--export",
        choices=("swagger2", "openapi3"),
        default=None,
        help="Print the selected version's API definition to stdout and exit",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("MIR_EMULATOR_USERNAME", auth.DEFAULT_USERNAME),
        help="Accepted username (env: MIR_EMULATOR_USERNAME)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("MIR_EMULATOR_PASSWORD", auth.DEFAULT_PASSWORD),
        help="Accepted password, pre-hash (env: MIR_EMULATOR_PASSWORD)",
    )
    parser.add_argument("--version", action="version", version=f"mir-emulator {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.export:
        import json

        from mir_emulator.spec import load_spec

        version, path = registry.spec_path(args.mir_version)
        doc = load_spec(path, version).document
        if args.export == "openapi3" and doc.get("swagger") == "2.0":
            from mir_emulator.openapi3 import to_openapi3

            doc = to_openapi3(doc)
        try:
            print(json.dumps(doc, indent=1))
        except BrokenPipeError:  # e.g. piped into `head` — not an error
            import os
            import sys

            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(devnull_fd, sys.stdout.fileno())
            finally:
                os.close(devnull_fd)
        return 0

    import uvicorn

    from mir_emulator.app import create_app

    app = create_app(
        args.mir_version,
        enforce_auth=not args.no_auth,
        username=args.username,
        password=args.password,
        cors=args.cors,
        mission_duration=args.mission_duration,
    )
    version = app.state.mir_version
    print(f"mir-emulator: MiR {version} REST API on http://{args.host}:{args.port}/api/v2.0.0")
    if not args.no_auth:
        print(
            "auth: Authorization: Basic BASE64(<user>:SHA-256(<password>)) — "
            f"default account {auth.DEFAULT_USERNAME!r}"
        )
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
