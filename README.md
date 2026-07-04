# MiR emulator

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
   REST API files listing (~500 PDFs, one per product per version), and
   applies the **selection rule** — *for every major release line, the
   latest patch of each of its newest 4 minor lines* (so both MiR software
   generations stay covered four API revisions deep).
2. MiR publishes the API definitions **as PDFs only** (swagger2markup +
   Asciidoctor renderings of their internal swagger doc). The scraper picks
   one robot PDF per selected version (MIR250 preferred; FLEET/HOOK are
   different APIs and excluded) and **converts it back to Swagger 2.0**
   (`mir_spec_scraper/pdf_convert.py`).
3. The converter is gated by a **correctness oracle**: MiR published exactly
   one machine-readable spec ever (3.5.4 `swagger.json`, pinned in the
   registry). Every scrape run re-converts the 3.5.4 PDF and requires zero
   structural differences (operations, parameters, response schemas,
   definition property types) against that official document.
4. New or changed specs land in
   `packages/mir-emulator/src/mir_emulator/specs/` + `registry.json` via an
   automated PR; **`ci.yml`** proves every tracked version against the full
   conformance + adversarial suite before it merges.
5. **`release.yml`** builds one `mir-emulator` distribution per tracked MiR
   version: `pip install mir-emulator==3.8.1` gets a 3.8.1 robot.

### Secrets

- `MIR_PORTAL_EMAIL` / `MIR_PORTAL_PASSWORD` — portal credentials (free
  account); without them the scrape workflow no-ops with a notice.
- `OPEN_ROUTER_API_KEY` (optional) — adds an AI-generated "release impact"
  summary on top of the mechanical API changelog in scrape PRs (model:
  `anthropic/claude-sonnet-5`, override with `MIR_SUMMARY_MODEL`). Strictly
  best-effort: any failure falls back to the mechanical report.
- `PYPI_API_TOKEN` (optional) — to publish wheels from `release.yml`.

## Tracked versions

`packages/mir-emulator/src/mir_emulator/specs/registry.json` is the
authoritative list (versions, hashes, provenance, source PDF URLs). Currently:

| MiR version | Source |
|---|---|
| 3.8.1 | Converted from the official MiR250 3.8.1 REST API PDF |
| 3.7.2 | Converted from the official MiR250 3.7.2 REST API PDF |
| 3.5.4 | Official `swagger.json` (pinned — the converter's oracle) |
| 2.14.7 | Converted from the official MiR250 2.14.7 REST API PDF |

## What the emulator does

- Serves **every operation** in the loaded spec under `/api/v2.0.0`
  (289 operations across 159 paths in 3.8.1), with responses that validate
  against the spec's response schemas. Deterministic: same requests, same
  answers.
- **Stateful** where it matters: `/status` (PUT merges), `/mission_queue`
  (POST enqueues `Pending` entries with monotonic ids, DELETE clears),
  `/registers` (200 PLC registers, read/write), generic CRUD with
  create/read/update/delete semantics everywhere else. `/metrics` speaks
  OpenMetrics.
- **MiR-style auth**: `Authorization: Basic BASE64(user:SHA-256(password))`,
  default account `distributor`/`distributor` like a factory robot. Override
  with `--username/--password` or disable with `--no-auth`.
- **Middleware-ready**: serves its own API definition at `/swagger.json`
  (Swagger 2.0, verbatim) and `/openapi.json` (OpenAPI 3.0, converted and
  round-trip-validated) so SDK generators and contract-testing tools can point
  straight at it; `--cors` for browser dashboards; `--export openapi3` to dump
  the definition; `Dockerfile` + `compose.yaml` for CI service stacks
  (`docker compose up` → MiR 3.8.1 on :8080 and 2.14.7 on :8081).

```python
from mir_emulator import create_app, supported_versions

app = create_app("3.8.1")   # ASGI app: run under uvicorn, or hit with httpx/TestClient
```

## Layout

- `packages/mir-emulator/` — the emulator library (Starlette; spec-driven
  routes + behavior overlays). Bundles all tracked spec files.
- `packages/mir-spec-scraper/` — portal login, listing parser, selection
  rule, registry updater.
- `tests/` — cross-version conformance, adversarial/negative-path, and
  real-TCP integration suites, parametrized over every tracked version.
- `scripts/build_versioned.py` — stamps and builds one wheel per MiR version.

## Hardening

Test harness: every tracked version runs the conformance suite (every
operation answered with a declared status, every GET body validated against
its response schema), an adversarial suite (auth bypass, injection, traversal,
oversized payloads, concurrency races), and a deterministic hypothesis fuzz
suite whose invariant is *no input produces a 5xx or a non-JSON error* — plus
real-TCP integration tests and an offline test of the full scraper state
machine (pinning, dropping, broken-file fallback). CI gates coverage at 82%
offline; the PDF converter's full-document path is gated live by the 3.5.4
oracle on every scrape.

Supply chain: SHA-pinned actions, `permissions: {}` + least-privilege jobs,
locked installs everywhere, Dependabot (uv + actions), CodeQL, PR dependency
review, and build-provenance attestations on release wheels. Details and hard
limits: [SECURITY.md](SECURITY.md).

Self-improvement loop: the weekly scrape opens PRs whose body is an **API
changelog** (added/removed/changed operations and definitions), re-validates
the converter against the pinned oracle first, survives individually broken
portal files by falling back to the last good spec of that major, and files a
`scraper-attention` issue whenever anything needed human eyes — a new major,
a new file format, or a PDF shape the converter can't parse cleanly.

## Dependencies (why each exists)

Runtime: `starlette` + `uvicorn` (ASGI routing/serving), `jsonschema`
(request/response validation against the spec), `pyyaml` (YAML specs),
`httpx` (scraper HTTP + test client), `pdfplumber` (scraper only: the portal
publishes PDFs, and its ruled-table extraction is what makes the PDF→swagger
conversion reliable). Dev: `pytest`, `hypothesis` (property tests on the
input-handling paths), `ruff`, `ty`.
