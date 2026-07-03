# mir-emulatro

Self-updating emulator of the [Mobile Industrial Robots (MiR) robot REST API](https://supportportal.mobile-industrial-robots.com/documentation/rest-api/rest-api-files/).
Develop and test MiR integrations against a local, spec-faithful `/api/v2.0.0`
without a robot — across the last three major MiR software generations.

```sh
./Quickstart -t          # lint + unit + conformance + integration
./Quickstart -s          # serve the newest tracked version on :8080

curl -H "Authorization: Basic $(printf '%s:%s' distributor "$(printf distributor | shasum -a 256 | cut -d' ' -f1)" | base64)" \
  http://127.0.0.1:8080/api/v2.0.0/status
```

## How it stays current

1. **`scrape.yml`** runs weekly: logs into the MiR support portal, parses the
   REST API files listing, and applies the **selection rule** — *the latest
   minor.patch of each of the newest 3 major versions* (filling remaining
   slots with the newest major's previous minor lines if fewer than 3 majors
   publish files).
2. New or changed files are downloaded, hashed, and committed to
   `packages/mir-emulator/src/mir_emulator/specs/` alongside
   `registry.json`, and a PR is opened.
3. **`ci.yml`** on that PR runs the full conformance + adversarial suite
   against every tracked spec — every endpoint answered with a declared
   status code, every GET body validated against its response schema — so a
   new MiR release is proven before it merges.
4. **`release.yml`** builds one `mir-emulator` distribution per tracked MiR
   version: `pip install mir-emulator==3.5.4` gets a 3.5.4 robot.

### Enabling the scraper

The portal has a login wall (free account). Add two repo secrets and the loop
is live:

- `MIR_PORTAL_EMAIL` / `MIR_PORTAL_PASSWORD` — portal credentials
- `PYPI_API_TOKEN` (optional) — to publish wheels from `release.yml`

Until then the scrape workflow no-ops with a notice, and the repo serves the
seed specs below.

## Tracked versions

See `packages/mir-emulator/src/mir_emulator/specs/registry.json` for the
authoritative list (versions, hashes, provenance). Seeded with:

| MiR version | Source | Notes |
|---|---|---|
| 3.5.4 | Official MiR250 `swagger.json` (mirrored from [processrobotics/mir-api](https://github.com/processrobotics/mir-api)) | newest 3.x line known publicly |
| 2.7.0 | Community OpenAPI conversion of the official 2.7.0 PDF ([osrf/mir100-client](https://github.com/osrf/mir100-client)) | stand-in until the scraper fetches the latest official 2.14.x |

The first authenticated scrape replaces the stand-ins with the portal's
official files per the selection rule (e.g. `3.5.x`, `3.4.x`, `2.14.x`).

## What the emulator does

- Serves **every operation** in the loaded spec under `/api/v2.0.0`
  (157 paths in 3.5.4), with responses that validate against the spec's
  response schemas. Deterministic: same requests, same answers.
- **Stateful** where it matters: `/status` (PUT merges), `/mission_queue`
  (POST enqueues `Pending` entries with monotonic ids, DELETE clears),
  `/registers` (200 PLC registers, read/write), generic CRUD with
  create/read/update/delete semantics everywhere else. `/metrics` speaks
  OpenMetrics.
- **MiR-style auth**: `Authorization: Basic BASE64(user:SHA-256(password))`,
  default account `distributor`/`distributor` like a factory robot. Override
  with `--username/--password` or disable with `--no-auth`.

```python
from mir_emulator import create_app, supported_versions

app = create_app("3.5.4")   # ASGI app: run under uvicorn, or hit with httpx/TestClient
```

## Layout

- `packages/mir-emulator/` — the emulator library (Starlette; spec-driven
  routes + behavior overlays). Bundles all tracked spec files.
- `packages/mir-spec-scraper/` — portal login, listing parser, selection
  rule, registry updater.
- `tests/` — cross-version conformance, adversarial/negative-path, and
  real-TCP integration suites, parametrized over every tracked version.
- `scripts/build_versioned.py` — stamps and builds one wheel per MiR version.

## Dependencies (why each exists)

Runtime: `starlette` + `uvicorn` (ASGI routing/serving), `jsonschema`
(request/response validation against the spec), `pyyaml` (YAML specs),
`httpx` (scraper HTTP + test client). Dev: `pytest`, `hypothesis`
(property tests on the input-handling paths), `ruff`, `ty`.
