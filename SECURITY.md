# Security

## Reporting

Open a private security advisory on GitHub (Security → Advisories → Report a
vulnerability). Do not open public issues for exploitable problems.

## Threat model in one paragraph

The emulator is a development/test double, not an internet-facing service: it
binds `127.0.0.1` by default, enforces MiR-style auth out of the box, caps
request bodies, sets `nosniff`/`no-store`/`DENY` headers, and is fuzzed in CI
with the invariant that no input can produce a 5xx or a non-JSON error. The
scraper treats the portal listing as untrusted input: downloads are restricted
to HTTPS on the portal's own host, size-capped, and converted output is
structurally validated against a pinned official document before it can enter
the registry. Nothing in this repository ever handles robot fleets or
production credentials other than the portal login, which lives exclusively in
GitHub secrets / a gitignored `.env`.

## Supply chain posture

- All GitHub Actions are pinned to full commit SHAs; workflows run with
  `permissions: {}` by default and least-privilege per job.
- `uv.lock` is enforced (`uv sync --locked`) in every workflow; Dependabot
  watches both `uv` and `github-actions` ecosystems; PRs run
  `dependency-review-action` (fail on any known-vulnerable addition).
- CodeQL (`security-and-quality`) runs on pushes, PRs, and weekly.
- Release wheels carry build-provenance attestations
  (`actions/attest-build-provenance`); verify with
  `gh attestation verify <wheel> --repo <this repo>`.
- Specs bundled into the emulator record their source URL and the SHA-256 of
  both the upstream file and the converted artifact (`registry.json`).

## Hard limits (enforced in code, tested)

| Limit | Value | Where |
|---|---|---|
| Request body | 2 MiB | emulator (`MAX_BODY_BYTES`) |
| Authorization header | 8 KiB | emulator (`MAX_HEADER_LENGTH`) |
| Portal download | 64 MiB | scraper (`MAX_DOWNLOAD_BYTES`) |
| Download origin | HTTPS, portal host only | scraper (`PortalClient.download`) |
